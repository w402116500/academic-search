"""候选相关性 Agent 的结构与证据验证测试。"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from app.core.workflow_settings import WorkflowSettings
from app.modules.search.contracts import RawCandidate, SourceName, TriageDecision, UnifiedCandidate
from app.modules.search.relevance import (
    CandidateRelevanceClaimVerificationFailure,
    CandidateRelevanceClaimVerificationResult,
    CandidateRelevanceContext,
    CandidateRelevanceEvaluationOutcome,
    CandidateRelevanceEvaluator,
    CandidateRelevanceStreamIdleTimeout,
    CandidateRelevanceTechnicalFailure,
    StructuredCandidateRelevanceClaimVerifier,
    collect_streamed_json_object,
)
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr


class FakeModel:
    """提供可控结构化结果，避免单元测试调用外部聊天模型。"""

    def __init__(self, response: object) -> None:
        self._response = response
        self.inputs: list[list[SystemMessage | HumanMessage]] = []

    async def ainvoke(self, input: list[SystemMessage | HumanMessage]) -> object:
        self.inputs.append(input)
        return self._response


class CapturingChatOpenAI:
    """记录模型构造参数，验证完整集合的输出预算不再固定截断。"""

    calls: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.calls.append(kwargs)

    def with_structured_output(self, *args: object, **kwargs: object) -> FakeModel:
        return FakeModel({})

    def bind(self, **_kwargs: object) -> FakeModel:
        return FakeModel({})


class StreamingModel:
    """按指定延迟发出消息块，验证超时只计相邻块的静默时间。"""

    def __init__(self, chunks: list[tuple[float, str]]) -> None:
        self._chunks = chunks

    async def ainvoke(self, input: list[SystemMessage | HumanMessage]) -> object:
        _ = input
        raise AssertionError("流式测试替身不能回退到 ainvoke。")

    async def astream(self, input: list[SystemMessage | HumanMessage]):
        _ = input
        for delay, content in self._chunks:
            await asyncio.sleep(delay)
            yield type("Chunk", (), {"content": content})()


class AcceptingClaimVerifier:
    """让理由核验通过，隔离评估器的其他结构和引文测试。"""

    async def verify(self, **kwargs: object) -> CandidateRelevanceClaimVerificationResult:
        assessments = kwargs["assessments"]
        assert isinstance(assessments, dict)
        return CandidateRelevanceClaimVerificationResult(
            verified_candidate_ids=frozenset(assessments),
            failures={},
        )


class RejectingClaimVerifier:
    """模拟独立核验发现理由扩大解释时的安全降级。"""

    async def verify(self, **kwargs: object) -> CandidateRelevanceClaimVerificationResult:
        assessments = kwargs["assessments"]
        assert isinstance(assessments, dict)
        candidate_id = next(iter(assessments))
        return CandidateRelevanceClaimVerificationResult(
            verified_candidate_ids=frozenset(),
            failures={
                candidate_id: CandidateRelevanceClaimVerificationFailure(
                    code="candidate_relevance_claim_unsupported",
                    message="候选理由中的 reason 无法由标题或摘要直接支持，已拒绝展示。",
                    retryable=False,
                )
            },
        )


def _candidate(
    abstract: str | None = "The study examines sleep quality and academic performance.",
) -> UnifiedCandidate:
    """创建经过统一规整与初筛的最小候选。"""
    source = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id="W1",
        title="Sleep quality and academic performance",
        abstract=abstract,
    )
    return UnifiedCandidate(
        candidate_id=uuid4(),
        title=source.title,
        title_key="sleep quality academic performance",
        abstract=abstract,
        source_records=(source,),
        triage=TriageDecision(included=True),
    )


def _context() -> CandidateRelevanceContext:
    """模拟已经由用户确认过的研究方向。"""
    return CandidateRelevanceContext(
        research_question="睡眠质量是否影响大学生学业表现？",
        direction_title="睡眠质量与学业表现",
        direction_summary="分析两者关联。",
        subtopics=("睡眠质量", "学业表现"),
        search_queries=("sleep quality academic performance",),
        start_year=2020,
        end_year=2026,
        languages=("zh", "en"),
    )


def _valid_assessment_payload(candidate: UnifiedCandidate) -> dict[str, object]:
    """构造可通过标题/摘要证据校验的单条模型结果。"""
    assert candidate.abstract is not None
    return {
        "candidate_id": str(candidate.candidate_id),
        "level": "core",
        "study_focus": "考察睡眠质量与学业表现之间的关系。",
        "reason": "研究对象和核心关系与当前方向一致。",
        "helpful_aspect": "可用于分析睡眠与学业表现的关联。",
        "limitations": [],
        "recommendation": "建议优先查看全文。",
        "evidence": [{"source_field": "abstract", "quote": candidate.abstract}],
    }


def test_payload_preserves_each_candidate_complete_abstract() -> None:
    """完整候选集合判断不能截断单篇摘要。"""
    candidate = _candidate("0123456789abcdef")

    payload = CandidateRelevanceEvaluator._build_payload(
        _context(),
        (candidate,),
    )

    serialized = json.loads(payload)
    assert serialized["candidates"][0]["abstract"] == "0123456789abcdef"


def test_complete_collection_output_budgets_scale_with_candidate_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """50 篇仍是单次集合调用，输出预算必须覆盖全部结构化结果。"""
    import app.infra.llm.candidate_relevance as candidate_relevance_module

    CapturingChatOpenAI.calls = []
    monkeypatch.setattr(candidate_relevance_module, "ChatOpenAI", CapturingChatOpenAI)
    settings = WorkflowSettings(
        deepseek_api_key=SecretStr("test"),
        workflow_relevance_output_tokens_per_candidate=700,
        workflow_relevance_verification_output_tokens_per_candidate=128,
    )

    candidate_relevance_module.build_candidate_relevance_model(settings, 50)
    candidate_relevance_module.build_candidate_relevance_verification_model(settings, 50)

    assert [call["max_tokens"] for call in CapturingChatOpenAI.calls] == [35_000, 6_400]
    assert all("timeout" not in call for call in CapturingChatOpenAI.calls)


def test_complete_collection_relevance_uses_activity_idle_timeout() -> None:
    """完整集合模型调用只监控流活动空闲，不保留总时长配置。"""
    settings = WorkflowSettings(deepseek_api_key=SecretStr("test"))

    assert settings.workflow_relevance_stream_idle_timeout_seconds == 120


@pytest.mark.asyncio
async def test_stream_collector_allows_long_total_duration_when_each_gap_has_activity() -> None:
    """多个短间隔可累积超过单一窗口，不能被总时长中断。"""
    result = await collect_streamed_json_object(
        StreamingModel([(0.01, '{"assess'), (0.01, 'ments":[]}')]),
        [],
        idle_timeout_seconds=0.02,
    )

    assert result == {"assessments": []}


@pytest.mark.asyncio
async def test_stream_collector_fails_only_after_an_idle_gap() -> None:
    """没有任何流活动超过空闲窗口才会停止等待。"""
    with pytest.raises(CandidateRelevanceStreamIdleTimeout):
        await collect_streamed_json_object(
            StreamingModel([(0.03, "{}")]),
            [],
            idle_timeout_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_stream_collector_treats_empty_chunks_as_activity() -> None:
    """JSON 内容可以延后到达，只要 SSE/模型持续有流块就不应超时。"""
    result = await collect_streamed_json_object(
        StreamingModel([(0.01, ""), (0.01, "{}")]),
        [],
        idle_timeout_seconds=0.05,
    )

    assert result == {}


@pytest.mark.asyncio
async def test_invalid_stream_json_becomes_a_safe_complete_collection_failure() -> None:
    """流拼接失败交给 Worker 重投，异常中不包含模型正文。"""
    candidate = _candidate()
    with pytest.raises(CandidateRelevanceTechnicalFailure) as raised:
        await CandidateRelevanceEvaluator(
            WorkflowSettings(deepseek_api_key=SecretStr("test")),
            model=StreamingModel([(0, '{"assessments":')]),
            claim_verifier=AcceptingClaimVerifier(),
        ).assess(context=_context(), candidates=(candidate,))

    assert raised.value.code == "candidate_relevance_output_invalid"
    assert "assessments" not in str(raised.value)


@pytest.mark.asyncio
async def test_invalid_outer_envelope_remains_a_complete_collection_failure() -> None:
    """只有单项可隔离；缺少数组外层时仍必须整体重试。"""
    candidate = _candidate()
    with pytest.raises(CandidateRelevanceTechnicalFailure) as raised:
        await CandidateRelevanceEvaluator(
            WorkflowSettings(deepseek_api_key=SecretStr("test")),
            model=FakeModel({"assessments": "not-an-array"}),
            claim_verifier=AcceptingClaimVerifier(),
        ).assess(context=_context(), candidates=(candidate,))

    assert raised.value.code == "candidate_relevance_output_invalid"


@pytest.mark.asyncio
async def test_model_evidence_must_be_found_in_unified_candidate() -> None:
    """模型引用不存在的原文时，只重试该候选而不展示伪理由。"""
    candidate = _candidate()
    evaluator = CandidateRelevanceEvaluator(
        WorkflowSettings(deepseek_api_key=SecretStr("test")),
        model=FakeModel(
            {
                "assessments": [
                    {
                        "candidate_id": str(candidate.candidate_id),
                        "level": "core",
                        "study_focus": "考察睡眠质量与学业表现的关系。",
                        "reason": "该研究直接相关。",
                        "helpful_aspect": "帮助分析变量关系。",
                        "limitations": [],
                        "recommendation": "优先获取全文。",
                        "evidence": [{"source_field": "abstract", "quote": "模型虚构的全文结论"}],
                    }
                ]
            }
        ),
        claim_verifier=AcceptingClaimVerifier(),
    )

    outcome = await evaluator.assess(context=_context(), candidates=(candidate,))

    assert outcome.resolved_candidates == ()
    assert outcome.retryable_failures[candidate.candidate_id].code == (
        "candidate_relevance_output_invalid"
    )


@pytest.mark.asyncio
async def test_invalid_item_keeps_valid_peer_and_retries_only_invalid_candidate() -> None:
    """无效证据只能阻止自己的候选，不应抹掉同批已核验结果。"""
    first = _candidate()
    second = _candidate("The study examines sleep quality and student wellbeing.")
    evaluator = CandidateRelevanceEvaluator(
        WorkflowSettings(deepseek_api_key=SecretStr("test")),
        model=FakeModel(
            {
                "assessments": [
                    {
                        "candidate_id": str(first.candidate_id),
                        "level": "core",
                        "study_focus": "考察睡眠质量与学业表现之间的关系。",
                        "reason": "研究对象和核心关系与当前方向一致。",
                        "helpful_aspect": "可用于分析睡眠与学业表现的关联。",
                        "limitations": [],
                        "recommendation": "建议优先查看全文。",
                        "evidence": [
                            {
                                "source_field": "abstract",
                                "quote": "sleep quality and academic performance",
                            }
                        ],
                    },
                    {
                        "candidate_id": str(second.candidate_id),
                        "level": "related",
                        "study_focus": "考察学生睡眠和心理健康。",
                        "reason": "可补充相关背景。",
                        "helpful_aspect": "帮助理解学生群体的睡眠问题。",
                        "limitations": [],
                        "recommendation": "可按需查看。",
                        "evidence": [{"source_field": "abstract", "quote": "模型编造的内容"}],
                    },
                ]
            }
        ),
        claim_verifier=AcceptingClaimVerifier(),
    )

    outcome = await evaluator.assess(context=_context(), candidates=(first, second))

    assert [candidate.candidate_id for candidate in outcome.resolved_candidates] == [
        first.candidate_id
    ]
    assert outcome.resolved_candidates[0].relevance_state == "completed"
    assert outcome.retryable_failures[second.candidate_id].code == (
        "candidate_relevance_output_invalid"
    )


@pytest.mark.asyncio
async def test_malformed_missing_and_duplicate_items_are_isolated_per_candidate() -> None:
    """可解析的外层数组必须隔离坏项目，让其他候选继续完成。"""
    valid = _candidate()
    missing = _candidate("The study examines missing candidate output.")
    duplicated = _candidate("The study examines duplicate candidate output.")
    malformed = _candidate("The study examines malformed candidate output.")
    evaluator = CandidateRelevanceEvaluator(
        WorkflowSettings(deepseek_api_key=SecretStr("test")),
        model=FakeModel(
            {
                "assessments": [
                    _valid_assessment_payload(valid),
                    _valid_assessment_payload(duplicated),
                    _valid_assessment_payload(duplicated),
                    {"candidate_id": str(malformed.candidate_id), "level": "core"},
                ]
            }
        ),
        claim_verifier=AcceptingClaimVerifier(),
    )

    outcome = await evaluator.assess(
        context=_context(),
        candidates=(valid, missing, duplicated, malformed),
    )

    assert isinstance(outcome, CandidateRelevanceEvaluationOutcome)
    assert [candidate.candidate_id for candidate in outcome.resolved_candidates] == [
        valid.candidate_id
    ]
    assert set(outcome.retryable_failures) == {
        missing.candidate_id,
        duplicated.candidate_id,
        malformed.candidate_id,
    }
    assert {failure.code for failure in outcome.retryable_failures.values()} == {
        "candidate_relevance_output_invalid"
    }


@pytest.mark.asyncio
async def test_empty_assessment_array_marks_all_candidates_retryable() -> None:
    """可解析的空数组表示每条有摘要候选都尚未形成评估。"""
    first = _candidate()
    second = _candidate("The study examines empty candidate output.")
    outcome = await CandidateRelevanceEvaluator(
        WorkflowSettings(deepseek_api_key=SecretStr("test")),
        model=FakeModel({"assessments": []}),
        claim_verifier=AcceptingClaimVerifier(),
    ).assess(context=_context(), candidates=(first, second))

    assert outcome.resolved_candidates == ()
    assert set(outcome.retryable_failures) == {first.candidate_id, second.candidate_id}


@pytest.mark.asyncio
async def test_technical_claim_verification_failure_keeps_verified_peer() -> None:
    """同批核验的临时失败不能影响已验证或终态拒绝的候选。"""
    verified = _candidate()
    unavailable = _candidate("The study examines sleep quality and student wellbeing.")
    unsupported = _candidate("The study examines sleep quality and student stress.")
    settings = WorkflowSettings(deepseek_api_key=SecretStr("test"))
    evaluator = CandidateRelevanceEvaluator(
        settings,
        model=FakeModel(
            {
                "assessments": [
                    _valid_assessment_payload(verified),
                    _valid_assessment_payload(unavailable),
                    _valid_assessment_payload(unsupported),
                ]
            }
        ),
        claim_verifier=StructuredCandidateRelevanceClaimVerifier(
            settings,
            model=FakeModel(
                {
                    "verifications": [
                        {
                            "candidate_id": str(verified.candidate_id),
                            "supported": True,
                            "unsupported_fields": [],
                        },
                        {
                            "candidate_id": str(unavailable.candidate_id),
                            "supported": False,
                            "unsupported_fields": [],
                        },
                        {
                            "candidate_id": str(unsupported.candidate_id),
                            "supported": False,
                            "unsupported_fields": ["reason"],
                        },
                    ]
                }
            ),
        ),
    )

    outcome = await evaluator.assess(
        context=_context(),
        candidates=(verified, unavailable, unsupported),
    )

    assert [candidate.candidate_id for candidate in outcome.resolved_candidates] == [
        verified.candidate_id,
        unsupported.candidate_id,
    ]
    assert outcome.resolved_candidates[0].relevance_state == "completed"
    assert outcome.resolved_candidates[1].relevance_state == "excluded"
    assert outcome.resolved_candidates[1].relevance_error is not None
    assert (
        outcome.resolved_candidates[1].relevance_error.code
        == "candidate_relevance_claim_unsupported"
    )
    assert outcome.retryable_failures[unavailable.candidate_id].code == (
        "candidate_relevance_claim_verification_invalid"
    )


@pytest.mark.asyncio
async def test_candidate_without_abstract_is_explicitly_insufficient() -> None:
    """缺摘要时不让模型猜测，直接返回信息不足状态。"""
    candidate = _candidate(abstract=None)
    evaluator = CandidateRelevanceEvaluator(
        WorkflowSettings(deepseek_api_key=SecretStr("test")),
        model=FakeModel({"assessments": []}),
        claim_verifier=AcceptingClaimVerifier(),
    )

    outcome = await evaluator.assess(context=_context(), candidates=(candidate,))

    assert outcome.retryable_failures == {}
    assert outcome.resolved_candidates[0].relevance_state == "excluded"
    assert outcome.resolved_candidates[0].relevance_assessment is not None
    assert outcome.resolved_candidates[0].relevance_assessment.level == "insufficient_information"
    assert outcome.resolved_candidates[0].relevance_assessment.study_focus.startswith(
        "目前只能从题目确认"
    )


@pytest.mark.asyncio
async def test_model_sees_complete_collection_and_keeps_missing_abstract_deterministic() -> None:
    """有摘要候选的模型判断仍能看见整组候选，缺摘要项不交给模型猜测。"""
    assessed = _candidate()
    missing_abstract = _candidate(abstract=None)
    model = FakeModel(
        {
            "assessments": [
                {
                    "candidate_id": str(assessed.candidate_id),
                    "level": "core",
                    "study_focus": "考察睡眠质量与学业表现之间的关系。",
                    "reason": "研究对象和核心关系与当前方向一致。",
                    "helpful_aspect": "可用于分析睡眠与学业表现的关联。",
                    "limitations": [],
                    "recommendation": "建议优先查看全文。",
                    "evidence": [
                        {
                            "source_field": "abstract",
                            "quote": "sleep quality and academic performance",
                        }
                    ],
                }
            ]
        }
    )
    evaluator = CandidateRelevanceEvaluator(
        WorkflowSettings(deepseek_api_key=SecretStr("test")),
        model=model,
        claim_verifier=AcceptingClaimVerifier(),
    )

    outcome = await evaluator.assess(
        context=_context(),
        candidates=(assessed, missing_abstract),
    )

    content = model.inputs[0][1].content
    assert isinstance(content, str)
    payload = json.loads(content)
    assert [item["candidate_id"] for item in payload["candidates"]] == [
        str(assessed.candidate_id),
        str(missing_abstract.candidate_id),
    ]
    assert outcome.retryable_failures == {}
    assert outcome.resolved_candidates[0].relevance_state == "completed"
    assert outcome.resolved_candidates[1].relevance_assessment is not None
    assert outcome.resolved_candidates[1].relevance_assessment.level == "insufficient_information"


@pytest.mark.asyncio
async def test_unverified_candidate_claims_are_rejected_instead_of_being_displayed() -> None:
    """理由的原文引文存在也不足以证明其中的扩大解释。"""
    candidate = _candidate()
    evaluator = CandidateRelevanceEvaluator(
        WorkflowSettings(deepseek_api_key=SecretStr("test")),
        model=FakeModel(
            {
                "assessments": [
                    {
                        "candidate_id": str(candidate.candidate_id),
                        "level": "core",
                        "study_focus": "考察睡眠质量与学业表现之间的关系。",
                        "reason": "该研究证明睡眠干预必然提升所有学生成绩。",
                        "helpful_aspect": "可用于分析变量关系。",
                        "limitations": [],
                        "recommendation": "建议优先查看全文。",
                        "evidence": [
                            {
                                "source_field": "abstract",
                                "quote": "sleep quality and academic performance",
                            }
                        ],
                    }
                ]
            }
        ),
        claim_verifier=RejectingClaimVerifier(),
    )

    outcome = await evaluator.assess(context=_context(), candidates=(candidate,))

    assert outcome.retryable_failures == {}
    assert outcome.resolved_candidates[0].relevance_state == "excluded"
    assert outcome.resolved_candidates[0].relevance_assessment is None
    assert outcome.resolved_candidates[0].relevance_error is not None
    assert (
        outcome.resolved_candidates[0].relevance_error.code
        == "candidate_relevance_claim_unsupported"
    )
    assert outcome.resolved_candidates[0].relevance_error.retryable is False
