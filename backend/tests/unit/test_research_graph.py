"""LangGraph 研究回答的离线行为测试。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Literal
from uuid import UUID

import pytest
from app.modules.agents.checkpoint import DirectResearchGraphExecutor
from app.modules.agents.contracts import (
    AnswerClaimDraft,
    AnswerClaimVerification,
    AnswerClaimVerificationItem,
    AnswerDraft,
    EvidenceVerification,
    FinalAnswerDraft,
    PresentationAnswerDraft,
    ResearchModelProtocolError,
    ResearchRouteDecision,
    ResearchRunCancelled,
    ResearchToolAction,
)
from app.modules.agents.evidence_refs import invalid_evidence_refs, validate_answer_cited_refs
from app.modules.agents.graph import ResearchGraphRunner
from app.modules.agents.prompts import (
    ROUTE_QUESTION_SYSTEM,
    answer_claim_verification_system,
    answer_system,
    presentation_editor_system,
)
from app.modules.rag.retrieval import RetrievalResult, RetrievedEvidence
from app.modules.research.contracts import ResearchRunStatus
from app.modules.research.execution_port import ResearchExecutionContext
from app.modules.research.settings import ResearchSettings
from pydantic import ValidationError

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000801")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000802")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000803")
_CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000000804")
_CHUNK_ID = UUID("00000000-0000-0000-0000-000000000805")
_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000806")
_INGESTION_RUN_ID = UUID("00000000-0000-0000-0000-000000000807")
_PAPER_ID = UUID("00000000-0000-0000-0000-000000000808")
_SECOND_CHUNK_ID = UUID("00000000-0000-0000-0000-000000000809")


def _context(question: str) -> ResearchExecutionContext:
    """构造不依赖数据库会话的已领取研究运行。"""
    return ResearchExecutionContext(
        research_run_id=_RUN_ID,
        conversation_id=_CONVERSATION_ID,
        collection_id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        question=question,
        mode="single_rag",
        langgraph_thread_id="unit-research-thread",
        model_config={},
    )


def _evidence() -> RetrievedEvidence:
    """构造一段带页码的已授权论文原文片段。"""
    return RetrievedEvidence(
        chunk_id=_CHUNK_ID,
        document_id=_DOCUMENT_ID,
        ingestion_run_id=_INGESTION_RUN_ID,
        paper_id=_PAPER_ID,
        content="The proposed method improves accuracy under the reported benchmark setting.",
        page_start=5,
        page_end=5,
        section_path=("Results",),
        locator={"paragraph": 2},
        title="A Research Paper",
        authors=({"literal": "Ada Lovelace"},),
        publication_year=2024,
        source_url="https://example.test/paper.pdf",
        vector_score=0.9,
        lexical_score=0.8,
        rrf_score=0.03,
        rank=1,
        source_chunk_ids=(_CHUNK_ID,),
    )


def _second_evidence() -> RetrievedEvidence:
    """构造第二段证据，用于验证 E-ref 与用户侧编号的重排。"""
    return replace(
        _evidence(),
        chunk_id=_SECOND_CHUNK_ID,
        content="A second passage supports the later claim.",
        rank=2,
        source_chunk_ids=(_SECOND_CHUNK_ID,),
    )


class FakeRetriever:
    """按查询顺序返回预设检索结果，记录图实际调用次数。"""

    def __init__(self, results: list[RetrievalResult]) -> None:
        self._results = iter(results)
        self.queries: list[str] = []

    async def retrieve(self, *, scope: object, query: str) -> RetrievalResult:
        self.queries.append(query)
        return next(self._results)


class FakeModel:
    """为图测试提供完全确定的结构化模型输出。"""

    def __init__(
        self,
        *,
        sufficient: bool = True,
        claims_supported: bool = True,
        second_claims_supported: bool = True,
        answer_text: str = "该结论由原文结果段支持。【E1】",
        cited_refs: tuple[str, ...] = ("E1",),
        supporting_refs: tuple[str, ...] = ("E1",),
        repaired_answer: str = "该结论由原文结果段支持。【E1】",
        presentation_answer: str | None = None,
        presentation_cited_refs: tuple[str, ...] | None = None,
        verification_claim: str | None = None,
        route_mode: Literal["single_rag", "multi_agent"] = "single_rag",
        route_protocol_failures: int = 0,
        compose_protocol_failures: int = 0,
    ) -> None:
        self.sufficient = sufficient
        self.claims_supported = claims_supported
        self.second_claims_supported = second_claims_supported
        self.answer_text = answer_text
        self.cited_refs = cited_refs
        self.supporting_refs = supporting_refs
        self.repaired_answer = repaired_answer
        self.presentation_answer = presentation_answer
        self.presentation_cited_refs = presentation_cited_refs
        self.verification_claim = verification_claim
        self.route_mode: Literal["single_rag", "multi_agent"] = route_mode
        self.route_protocol_failures = route_protocol_failures
        self.compose_protocol_failures = compose_protocol_failures
        self.route_calls = 0
        self.rewrite_count = 0
        self.verify_answer_calls = 0
        self.compose_calls = 0
        self.presentation_edit_calls = 0
        self.verify_cited_refs: list[tuple[str, ...]] = []
        self.presentation_supported_claims: list[tuple[str, ...]] = []
        self.presentation_allowed_refs: list[tuple[str, ...]] = []

    async def rewrite_query(self, question: str) -> str:
        self.rewrite_count += 1
        return f"rewritten: {question}"

    async def route_question(self, question: str) -> ResearchRouteDecision:
        self.route_calls += 1
        if self.route_protocol_failures:
            self.route_protocol_failures -= 1
            raise ResearchModelProtocolError("路由模型遗漏了必填 mode 字段。")
        return ResearchRouteDecision(
            mode=self.route_mode,
            reason="问题需要分别核验多个方面。"
            if self.route_mode == "multi_agent"
            else "问题可以由同一组原文证据直接核验。",
        )

    async def generate_answer(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> AnswerDraft:
        assert evidences
        cited_refs = list(self.cited_refs if self.sufficient else ())
        return AnswerDraft(
            answer=self.answer_text if self.sufficient else "当前证据不足。",
            cited_refs=cited_refs,
            claims=[
                AnswerClaimDraft(
                    claim_id="C1",
                    text="该结论由原文结果段支持",
                    refs=cited_refs,
                )
            ]
            if self.sufficient
            else [],
            evidence_sufficient=self.sufficient,
            clarification_question="请限定实验条件。" if not self.sufficient else None,
        )

    async def plan_subquestions(self, question: str, max_subquestions: int) -> tuple[str, ...]:
        return ("方法差异是什么？", "结果差异是什么？")

    async def decide_research_action(
        self,
        *,
        question: str,
        available_queries: Sequence[str],
        observations: Sequence[dict[str, object]],
        tool_calls_remaining: int,
    ) -> ResearchToolAction:
        if available_queries:
            return ResearchToolAction(
                action="retrieve",
                query=available_queries[0],
                reason="还有未核验的子问题。",
            )
        return ResearchToolAction(action="answer", reason="已获得全部规划子问题的观察结果。")

    async def verify_evidence(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> EvidenceVerification:
        return EvidenceVerification(
            supported_refs=[f"E{index}" for index, _ in enumerate(evidences, start=1)]
        )

    async def verify_answer_claims(
        self,
        *,
        question: str,
        answer: str,
        evidences: Sequence[RetrievedEvidence],
        cited_refs: Sequence[str],
    ) -> AnswerClaimVerification:
        assert question
        assert evidences
        self.verify_cited_refs.append(tuple(cited_refs))
        self.verify_answer_calls += 1
        supported = (
            self.claims_supported if self.verify_answer_calls == 1 else self.second_claims_supported
        )
        return AnswerClaimVerification(
            claims=[
                AnswerClaimVerificationItem(
                    claim_id="C1",
                    claim=self.verification_claim or answer,
                    supported=supported,
                    supporting_refs=list(self.supporting_refs) if supported else [],
                )
            ]
        )

    async def compose_final_answer(
        self,
        *,
        question: str,
        draft_answer: str,
        verification: AnswerClaimVerification,
        evidences: Sequence[RetrievedEvidence],
    ) -> FinalAnswerDraft:
        assert question
        assert draft_answer
        assert verification.claims
        assert evidences
        self.compose_calls += 1
        if self.compose_protocol_failures:
            self.compose_protocol_failures -= 1
            raise ResearchModelProtocolError("最终答案编辑器返回了不符合结构约束的结果。")
        return FinalAnswerDraft(
            answer=self.repaired_answer,
            cited_refs=["E1"],
            resolved_claim_ids=["C1"],
            evidence_insufficient_claims=[],
        )

    async def edit_answer_presentation(
        self,
        *,
        question: str,
        supported_claims: Sequence[AnswerClaimVerificationItem],
        allowed_refs: Sequence[str],
    ) -> PresentationAnswerDraft:
        assert question
        self.presentation_edit_calls += 1
        self.presentation_supported_claims.append(tuple(item.claim for item in supported_claims))
        self.presentation_allowed_refs.append(tuple(allowed_refs))
        return PresentationAnswerDraft(
            answer=self.presentation_answer or self.answer_text,
            cited_refs=list(self.presentation_cited_refs or self.cited_refs),
        )


@pytest.mark.parametrize("alias", ["router", "choice", "agent", "route", "selection"])
def test_structured_router_accepts_known_model_aliases(alias: str) -> None:
    """真实 OpenAI 兼容模型的已知别名应归一到稳定 mode 契约。"""
    decision = ResearchRouteDecision.model_validate(
        {alias: "single_rag", "reason": "问题无需多源比较。"}
    )

    assert decision.mode == "single_rag"


def test_structured_router_rejects_answer_payload_without_route_mode() -> None:
    """模型把研究结论塞进 content 时，不能被宽松解析为某条路由。"""
    with pytest.raises(ValidationError, match="mode"):
        ResearchRouteDecision.model_validate(
            {
                "reason": "该问题适合单篇研究。",
                "content": {
                    "study_population": "306 名医学生",
                    "prevalence_of_poor_sleep_quality": "53.4%",
                },
            }
        )


def test_route_prompt_requires_explicit_top_level_decision_contract() -> None:
    """JSON mode 不传字段定义时，路由提示本身必须固定顶层契约。"""
    assert "顶层 JSON 对象" in ROUTE_QUESTION_SYSTEM
    assert '"mode"' in ROUTE_QUESTION_SYSTEM
    assert '"reason"' in ROUTE_QUESTION_SYSTEM
    assert "不得使用 content、data、result 等包装字段" in ROUTE_QUESTION_SYSTEM


@pytest.mark.asyncio
async def test_single_rag_retries_one_route_protocol_failure_within_model_budget() -> None:
    """路由首次漏字段时可重试一次，且重试必须计入真实模型调用预算。"""
    model = FakeModel(route_protocol_failures=1)
    outcome = await ResearchGraphRunner(
        retriever=FakeRetriever([RetrievalResult(evidences=(_evidence(),), trace={"final": 1})]),
        model=model,
        settings=ResearchSettings(),
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("该方法的实验结果是什么？"))

    assert outcome.status is ResearchRunStatus.COMPLETED
    assert model.route_calls == 2
    assert outcome.retrieval_trace["routing"]["route_attempts"] == 2
    assert outcome.retrieval_trace["budget"]["model_calls"] == 4


@pytest.mark.asyncio
async def test_route_protocol_failure_remains_failed_after_one_retry() -> None:
    """第二次路由仍不合约时不得默认选路或发布澄清回答。"""
    model = FakeModel(route_protocol_failures=2)

    with pytest.raises(ResearchModelProtocolError) as error:
        await ResearchGraphRunner(
            retriever=FakeRetriever([]),
            model=model,
            settings=ResearchSettings(),
            graph_executor=DirectResearchGraphExecutor(),
        ).run(_context("该方法的实验结果是什么？"))

    assert model.route_calls == 2
    assert error.value.diagnostics == {
        "model_output_summary": "structured_output_rejected",
        "route_attempts": 2,
    }


def test_answer_draft_allows_empty_citations_only_for_insufficient_evidence() -> None:
    """证据不足是可见正常终态，不能因空引用被误判为模型协议故障。"""
    clarification = AnswerDraft(
        answer="当前证据不足。",
        cited_refs=[],
        claims=[],
        evidence_sufficient=False,
        clarification_question="请补充原文。",
    )
    assert clarification.cited_refs == []

    with pytest.raises(ValidationError, match="必须至少引用"):
        AnswerDraft(answer="结论成立。", cited_refs=[], claims=[], evidence_sufficient=True)

    with pytest.raises(ValidationError):
        AnswerDraft(
            answer="结论成立。",
            cited_refs=[str(_CHUNK_ID)],
            claims=[],
            evidence_sufficient=True,
        )


def test_answer_draft_normalizes_model_claim_field_and_missing_claim_id() -> None:
    """真实模型可能输出 claim+refs；后端补齐 claim_id/text 后再做严格校验。"""
    draft = AnswerDraft.model_validate(
        {
            "answer": "睡眠质量与学习效率相关。【E1】",
            "cited_refs": ["E1"],
            "claims": [{"claim": "睡眠质量与学习效率相关。", "refs": ["E1"]}],
            "evidence_sufficient": True,
            "clarification_question": "",
        }
    )

    assert draft.claims[0].claim_id == "C1"
    assert draft.claims[0].text == "睡眠质量与学习效率相关。"
    assert draft.claims[0].refs == ["E1"]


def test_answer_claim_verification_normalizes_missing_claim_id() -> None:
    """verifier 漏 claim_id 时按声明顺序补齐，供后续 composer 引用。"""
    verification = AnswerClaimVerification.model_validate(
        {
            "claims": [
                {"claim": "第一条主张", "supported": True, "supporting_refs": ["E1"]},
                {"claim": "第二条主张", "supported": False, "supporting_refs": []},
            ]
        }
    )

    assert [claim.claim_id for claim in verification.claims] == ["C1", "C2"]


def test_answer_claim_verification_wraps_root_claim_list() -> None:
    """verifier 直接返回 claim 数组时仍收敛为稳定 claims 对象。"""
    verification = AnswerClaimVerification.model_validate(
        [{"claim_id": "C1", "claim": "第一条主张", "supported": True, "supporting_refs": ["E1"]}]
    )

    assert verification.claims[0].claim_id == "C1"
    assert verification.claims[0].supporting_refs == ["E1"]


def test_rag_prompts_expose_only_evidence_refs_not_chunk_ids() -> None:
    """模型提示只能出现 E-ref，不应暴露数据库 chunk UUID。"""
    evidence = _evidence()
    answer_prompt = answer_system((evidence,))
    verifier_prompt = answer_claim_verification_system((evidence,))

    assert "[E1]" in answer_prompt
    assert "[E1]" in verifier_prompt
    assert "两个独立必填约束" in answer_prompt
    assert "chunk_id=" not in answer_prompt
    assert "chunk_id=" not in verifier_prompt
    assert str(evidence.chunk_id) not in answer_prompt
    assert str(evidence.chunk_id) not in verifier_prompt


def test_presentation_editor_prompt_has_no_evidence_block_or_uuid() -> None:
    """展示编辑器只接受闭合的已支持主张集，不能接触原文证据。"""
    prompt = presentation_editor_system()
    evidence = _evidence()

    assert "原始证据内容" in prompt
    assert evidence.content not in prompt
    assert str(evidence.chunk_id) not in prompt
    assert "chunk_id" not in prompt
    assert "UUID" not in prompt


def test_verifier_prompt_uses_cited_subset_without_renumbering_refs() -> None:
    """只给 verifier 实际引用片段，但保留原 EvidenceSnapshot 内的 E-ref。"""
    first = _evidence()
    second = _second_evidence()

    verifier_prompt = answer_claim_verification_system(
        (first, second),
        cited_refs=("E2",),
    )

    assert "回答已引用 E 序号：E2" in verifier_prompt
    assert "[E2]" in verifier_prompt
    assert second.content in verifier_prompt
    assert "[E1]" not in verifier_prompt
    assert first.content not in verifier_prompt


def test_claim_verifier_contract_rejects_verdict_ref_mismatch() -> None:
    """无出处支持和拒绝主张携带 refs 都属于 verifier 协议错误。"""
    with pytest.raises(ValidationError, match="必须关联至少一个引用片段"):
        AnswerClaimVerificationItem(
            claim_id="C1",
            claim="结论成立",
            supported=True,
            supporting_refs=[],
        )

    with pytest.raises(ValidationError, match="不能携带支持片段"):
        AnswerClaimVerificationItem(
            claim_id="C1",
            claim="结论成立",
            supported=False,
            supporting_refs=["E1"],
        )


def test_evidence_ref_validation_rejects_unknown_and_uuid_leakage() -> None:
    """E9 和 UUID 都不能穿过模型侧 EvidenceRef 边界。"""
    invalid = invalid_evidence_refs(("E1", "E9", str(_CHUNK_ID)), {"E1"})

    assert invalid == ("E9", str(_CHUNK_ID))

    with pytest.raises(ValueError, match="Unknown evidence ref"):
        validate_answer_cited_refs("错误引用【E9】。", (_evidence(),), ("E9",))


@pytest.mark.asyncio
async def test_single_rag_only_uses_retrieved_evidence_for_citation() -> None:
    """单轮回答应完成并只引用 Retriever 返回的当前集合片段。"""
    retriever = FakeRetriever([RetrievalResult(evidences=(_evidence(),), trace={"final": 1})])
    model = FakeModel()
    outcome = await ResearchGraphRunner(
        retriever=retriever,
        model=model,
        settings=ResearchSettings(),
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("该方法的实验结果是什么？"))

    assert outcome.status is ResearchRunStatus.COMPLETED
    assert outcome.cited_chunk_ids == (_CHUNK_ID,)
    assert outcome.answer == "该结论由原文结果段支持。[1]"
    assert outcome.evidences[0].page_start == 5
    assert retriever.queries == ["该方法的实验结果是什么？"]
    assert model.presentation_edit_calls == 0


@pytest.mark.asyncio
async def test_single_rag_edits_fragmented_supported_answer_then_reverifies() -> None:
    """同一来源连续三句仅在首轮全支持时进入一次展示编辑和二次核验。"""
    writer_answer = (
        "研究结论显示该方法有效【E1】。"
        "研究结论也显示该方法稳定【E1】。"
        "研究结论还显示该方法可复现【E1】。"
    )
    editor_answer = "现有证据总体支持该方法有效、稳定且可复现这一研究结论【E1】。"
    model = FakeModel(
        answer_text=writer_answer,
        presentation_answer=editor_answer,
        verification_claim="研究结论",
    )
    outcome = await ResearchGraphRunner(
        retriever=FakeRetriever([RetrievalResult(evidences=(_evidence(),), trace={"final": 1})]),
        model=model,
        settings=ResearchSettings(),
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("该方法的实验结果是什么？"))

    assert outcome.status is ResearchRunStatus.COMPLETED
    assert outcome.answer == "现有证据总体支持该方法有效、稳定且可复现这一研究结论[1]。"
    assert model.presentation_edit_calls == 1
    assert model.verify_answer_calls == 2
    assert model.presentation_supported_claims == [("研究结论",)]
    assert model.presentation_allowed_refs == [("E1",)]
    assert outcome.retrieval_trace["presentation_quality"] == {
        "citation_fragmentation": {
            "triggered": True,
            "citation_bearing_sentence_count": 3,
            "max_same_ref_sentence_run": 3,
            "repeated_ref": "E1",
        },
        "writer_answer": writer_answer,
        "presentation_edit": {"status": "applied", "editor_answer": editor_answer},
    }


@pytest.mark.asyncio
async def test_single_rag_renders_user_citations_by_first_use_order() -> None:
    """E-ref 是模型协议；用户侧引用号应按最终回答首次出现顺序重新编号。"""
    retriever = FakeRetriever(
        [RetrievalResult(evidences=(_evidence(), _second_evidence()), trace={"final": 2})]
    )
    outcome = await ResearchGraphRunner(
        retriever=retriever,
        model=FakeModel(
            answer_text="第二段先被引用【E2】，第一段随后被引用【E1】。",
            cited_refs=("E2", "E1"),
        ),
        settings=ResearchSettings(),
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("请按证据回答。"))

    assert outcome.status is ResearchRunStatus.COMPLETED
    assert outcome.answer == "第二段先被引用[1]，第一段随后被引用[2]。"
    assert outcome.cited_chunk_ids == (_SECOND_CHUNK_ID, _CHUNK_ID)
    assert outcome.retrieval_trace["user_citations"] == [
        {"display_index": 1, "evidence_ref": "E2", "chunk_id": str(_SECOND_CHUNK_ID)},
        {"display_index": 2, "evidence_ref": "E1", "chunk_id": str(_CHUNK_ID)},
    ]


@pytest.mark.asyncio
async def test_single_rag_rejects_cited_refs_that_do_not_match_answer_text() -> None:
    """正文引用和结构化 cited_refs 不一致时，不能由任一侧静默覆盖另一侧。"""
    retriever = FakeRetriever(
        [RetrievalResult(evidences=(_evidence(), _second_evidence()), trace={"final": 2})]
    )
    model = FakeModel(
        answer_text="第二段被引用【E2】。",
        cited_refs=("E1",),
        supporting_refs=("E2",),
    )

    with pytest.raises(ResearchModelProtocolError, match="正文引用无效"):
        await ResearchGraphRunner(
            retriever=retriever,
            model=model,
            settings=ResearchSettings(),
            graph_executor=DirectResearchGraphExecutor(),
        ).run(_context("请按证据回答。"))

    assert model.verify_cited_refs == []


@pytest.mark.asyncio
async def test_single_rag_rejects_verifier_supporting_uncited_ref() -> None:
    """verifier 不能用答案未引用的 E-ref 来支撑主张。"""
    retriever = FakeRetriever(
        [RetrievalResult(evidences=(_evidence(), _second_evidence()), trace={"final": 2})]
    )
    model = FakeModel(
        answer_text="第一段被引用【E1】。",
        cited_refs=("E1",),
        supporting_refs=("E2",),
    )

    with pytest.raises(ResearchModelProtocolError, match="未被回答实际引用") as error:
        await ResearchGraphRunner(
            retriever=retriever,
            model=model,
            settings=ResearchSettings(),
            graph_executor=DirectResearchGraphExecutor(),
        ).run(_context("请按证据回答。"))

    assert model.verify_cited_refs == [("E1",)]
    assert error.value.diagnostics == {
        "model_output_summary": "structured_output_rejected",
        "evidence_snapshot": [
            {
                "evidence_ref": "E1",
                "chunk_id": str(_CHUNK_ID),
                "rank": 1,
                "title": "A Research Paper",
                "page_start": 5,
                "page_end": 5,
            },
            {
                "evidence_ref": "E2",
                "chunk_id": str(_SECOND_CHUNK_ID),
                "rank": 2,
                "title": "A Research Paper",
                "page_start": 5,
                "page_end": 5,
            },
        ],
    }


@pytest.mark.asyncio
async def test_single_rag_rewrites_at_most_once_then_requests_clarification() -> None:
    """空检索不能无限改写，达到一次预算后必须以澄清状态结束。"""
    retriever = FakeRetriever(
        [
            RetrievalResult(evidences=(), trace={"final": 0}),
            RetrievalResult(evidences=(), trace={"final": 0}),
        ]
    )
    model = FakeModel()
    outcome = await ResearchGraphRunner(
        retriever=retriever,
        model=model,
        settings=ResearchSettings(rag_max_query_rewrites=1),
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("没有足够上下文的问题"))

    assert outcome.status is ResearchRunStatus.AWAITING_CLARIFICATION
    assert model.rewrite_count == 1
    assert len(retriever.queries) == 2
    assert outcome.cited_chunk_ids == ()


@pytest.mark.asyncio
async def test_single_rag_rejects_an_answer_with_an_unsupported_atomic_claim() -> None:
    """不支持的主张先进入 composer 修复，修复后还要再次核验。"""
    retriever = FakeRetriever([RetrievalResult(evidences=(_evidence(),), trace={"final": 1})])
    model = FakeModel(claims_supported=False)
    outcome = await ResearchGraphRunner(
        retriever=retriever,
        model=model,
        settings=ResearchSettings(),
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("该方法的实验结果是什么？"))

    assert outcome.status is ResearchRunStatus.COMPLETED
    assert outcome.answer == "该结论由原文结果段支持。[1]"
    assert outcome.cited_chunk_ids == (_CHUNK_ID,)
    assert model.compose_calls == 1
    assert model.verify_answer_calls == 2
    assert model.presentation_edit_calls == 0
    assert outcome.retrieval_trace["answer_claim_verification"]["status"] == "supported"


@pytest.mark.asyncio
async def test_single_rag_retries_one_repair_composer_protocol_failure() -> None:
    """最终答案编辑器首次结构化失败时可重试一次，成功后仍需二次 verifier。"""
    retriever = FakeRetriever([RetrievalResult(evidences=(_evidence(),), trace={"final": 1})])
    model = FakeModel(claims_supported=False, compose_protocol_failures=1)
    outcome = await ResearchGraphRunner(
        retriever=retriever,
        model=model,
        settings=ResearchSettings(),
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("该方法的实验结果是什么？"))

    assert outcome.status is ResearchRunStatus.COMPLETED
    assert outcome.answer == "该结论由原文结果段支持。[1]"
    assert model.compose_calls == 2
    assert model.verify_answer_calls == 2
    assert outcome.retrieval_trace["answer_repair"] == {
        "status": "completed",
        "protocol_attempts": 2,
    }
    assert outcome.retrieval_trace["budget"]["model_calls"] == 6


@pytest.mark.asyncio
async def test_single_rag_clarifies_when_repair_composer_protocol_retry_fails() -> None:
    """最终答案编辑器连续结构化失败时不能发布未经二次核验的回答。"""
    retriever = FakeRetriever([RetrievalResult(evidences=(_evidence(),), trace={"final": 1})])
    model = FakeModel(claims_supported=False, compose_protocol_failures=2)
    outcome = await ResearchGraphRunner(
        retriever=retriever,
        model=model,
        settings=ResearchSettings(),
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("该方法的实验结果是什么？"))

    assert outcome.status is ResearchRunStatus.AWAITING_CLARIFICATION
    assert "缩小问题范围" in outcome.answer
    assert outcome.cited_chunk_ids == ()
    assert model.compose_calls == 2
    assert model.verify_answer_calls == 1
    assert outcome.retrieval_trace["answer_repair"] == {
        "status": "fallback",
        "fallback_reason": "protocol_error",
        "protocol_attempts": 2,
    }


@pytest.mark.asyncio
async def test_complex_question_uses_structured_routing_and_evidence_verification() -> None:
    """结构化路由可让不含关键词的同义表达进入受限复杂模式。"""
    retriever = FakeRetriever(
        [
            RetrievalResult(evidences=(_evidence(),), trace={"final": 1}),
            RetrievalResult(evidences=(_evidence(),), trace={"final": 1}),
        ]
    )
    outcome = await ResearchGraphRunner(
        retriever=retriever,
        model=FakeModel(route_mode="multi_agent"),
        settings=ResearchSettings(),
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("请分别归纳两组研究证据在方法和结果上的不同。"))

    assert outcome.status is ResearchRunStatus.COMPLETED
    assert outcome.mode == "multi_agent"
    assert outcome.retrieval_trace["tool_calls"] == 2
    assert outcome.retrieval_trace["routing"] == {
        "classifier": "structured_question_router",
        "mode": "multi_agent",
        "reason": "问题需要分别核验多个方面。",
    }
    assert len(outcome.retrieval_trace["react_steps"]) == 3
    assert outcome.retrieval_trace["budget"]["tool_calls"] == 2
    assert outcome.cited_chunk_ids == (_CHUNK_ID,)


@pytest.mark.asyncio
async def test_complex_rag_edits_only_directly_supported_fragmented_answer() -> None:
    """复杂研究的直通全支持分支与单轮路径使用同一展示编辑约束。"""
    writer_answer = "研究结论有效【E1】。研究结论稳定【E1】。研究结论可复现【E1】。"
    model = FakeModel(
        route_mode="multi_agent",
        answer_text=writer_answer,
        presentation_answer="现有原文一致支持这一研究结论有效、稳定且可复现【E1】。",
        verification_claim="研究结论",
    )
    outcome = await ResearchGraphRunner(
        retriever=FakeRetriever(
            [
                RetrievalResult(evidences=(_evidence(),), trace={"final": 1}),
                RetrievalResult(evidences=(_evidence(),), trace={"final": 1}),
            ]
        ),
        model=model,
        settings=ResearchSettings(),
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("请分别归纳两组研究证据在方法和结果上的不同。"))

    assert outcome.status is ResearchRunStatus.COMPLETED
    assert outcome.answer == "现有原文一致支持这一研究结论有效、稳定且可复现[1]。"
    assert model.presentation_edit_calls == 1
    assert model.verify_answer_calls == 2
    assert (
        outcome.retrieval_trace["presentation_quality"]["presentation_edit"]["status"] == "applied"
    )


@pytest.mark.asyncio
async def test_complex_research_answers_from_evidence_when_tool_budget_is_reached() -> None:
    """工具预算耗尽后不再继续调用 Retriever，而是用已获证据进入核验和回答。"""
    retriever = FakeRetriever([RetrievalResult(evidences=(_evidence(),), trace={"final": 1})])
    outcome = await ResearchGraphRunner(
        retriever=retriever,
        model=FakeModel(route_mode="multi_agent"),
        settings=ResearchSettings(rag_max_react_tool_calls=1),
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("请分别归纳两组研究证据在方法和结果上的不同。"))

    assert outcome.status is ResearchRunStatus.COMPLETED
    assert outcome.retrieval_trace["budget_exhausted"] is True
    assert outcome.retrieval_trace["budget"]["tool_calls"] == 1
    assert len(retriever.queries) == 1


@pytest.mark.asyncio
async def test_cancellation_checker_stops_before_a_following_graph_node() -> None:
    """图在模型调用返回后的安全边界观察取消，不会继续写入回答。"""
    checks = 0

    async def cancellation_checker() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(ResearchRunCancelled):
        await ResearchGraphRunner(
            retriever=FakeRetriever(
                [RetrievalResult(evidences=(_evidence(),), trace={"final": 1})]
            ),
            model=FakeModel(),
            settings=ResearchSettings(),
            graph_executor=DirectResearchGraphExecutor(),
            cancellation_checker=cancellation_checker,
        ).run(_context("该方法的实验结果是什么？"))
