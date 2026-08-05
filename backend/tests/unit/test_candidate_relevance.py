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
    CandidateRelevanceEvaluator,
    CandidateRelevanceStreamIdleTimeout,
    CandidateRelevanceTechnicalFailure,
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
async def test_model_evidence_must_be_found_in_unified_candidate() -> None:
    """模型引用不存在的原文时，候选必须明确失败而不是展示伪理由。"""
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

    with pytest.raises(CandidateRelevanceTechnicalFailure) as raised:
        await evaluator.assess(context=_context(), candidates=(candidate,))

    assert raised.value.code == "candidate_relevance_output_invalid"


@pytest.mark.asyncio
async def test_invalid_item_retries_the_complete_collection() -> None:
    """任一候选结构无效时，不能把不完整集合当作已完成结果。"""
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

    with pytest.raises(CandidateRelevanceTechnicalFailure) as raised:
        await evaluator.assess(context=_context(), candidates=(first, second))

    assert raised.value.code == "candidate_relevance_output_invalid"


@pytest.mark.asyncio
async def test_candidate_without_abstract_is_explicitly_insufficient() -> None:
    """缺摘要时不让模型猜测，直接返回信息不足状态。"""
    candidate = _candidate(abstract=None)
    evaluator = CandidateRelevanceEvaluator(
        WorkflowSettings(deepseek_api_key=SecretStr("test")),
        model=FakeModel({"assessments": []}),
        claim_verifier=AcceptingClaimVerifier(),
    )

    result = await evaluator.assess(context=_context(), candidates=(candidate,))

    assert result[0].relevance_state == "excluded"
    assert result[0].relevance_assessment is not None
    assert result[0].relevance_assessment.level == "insufficient_information"
    assert result[0].relevance_assessment.study_focus.startswith("目前只能从题目确认")


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

    result = await evaluator.assess(
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
    assert result[0].relevance_state == "completed"
    assert result[1].relevance_assessment is not None
    assert result[1].relevance_assessment.level == "insufficient_information"


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

    result = await evaluator.assess(context=_context(), candidates=(candidate,))

    assert result[0].relevance_state == "excluded"
    assert result[0].relevance_assessment is None
    assert result[0].relevance_error is not None
    assert result[0].relevance_error.code == "candidate_relevance_claim_unsupported"
    assert result[0].relevance_error.retryable is False
