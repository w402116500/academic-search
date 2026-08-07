"""默认 Fast RAG 路径的离线行为测试。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

import pytest
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
    ResearchToolAction,
)
from app.modules.agents.fast_rag import FastRagRunner
from app.modules.rag.retrieval import RetrievalResult, RetrievedEvidence
from app.modules.research.contracts import (
    ResearchQuestionMode,
    ResearchRunStage,
    ResearchRunStatus,
)
from app.modules.research.execution_port import ResearchExecutionContext
from app.modules.research.question_mode import resolve_research_execution_mode
from app.modules.research.settings import ResearchSettings

_OWNER_ID = UUID("00000000-0000-0000-0000-000000001801")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000001802")
_RUN_ID = UUID("00000000-0000-0000-0000-000000001803")
_CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000001804")
_CHUNK_ID = UUID("00000000-0000-0000-0000-000000001805")
_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000001806")
_INGESTION_RUN_ID = UUID("00000000-0000-0000-0000-000000001807")
_PAPER_ID = UUID("00000000-0000-0000-0000-000000001808")
_SECOND_CHUNK_ID = UUID("00000000-0000-0000-0000-000000001809")


def _context(question: str) -> ResearchExecutionContext:
    return ResearchExecutionContext(
        research_run_id=_RUN_ID,
        conversation_id=_CONVERSATION_ID,
        collection_id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        question=question,
        mode="single_rag",
        langgraph_thread_id="fast-rag-unit-thread",
        model_config={},
    )


def _evidence() -> RetrievedEvidence:
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
    return replace(
        _evidence(),
        chunk_id=_SECOND_CHUNK_ID,
        content="A second passage supports the later claim.",
        rank=2,
        source_chunk_ids=(_SECOND_CHUNK_ID,),
    )


class FakeRetriever:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.queries: list[str] = []

    async def retrieve(self, *, scope: object, query: str) -> RetrievalResult:
        self.queries.append(query)
        return self.result


class FastOnlyModel:
    """只允许快速路径调用 Writer，其余严格链路调用会直接失败。"""

    def __init__(
        self,
        *,
        sufficient: bool = True,
        answer_text: str = "该结论由原文结果段支持。【E1】",
        cited_refs: tuple[str, ...] = ("E1",),
    ) -> None:
        self.sufficient = sufficient
        self.answer_text = answer_text
        self.cited_refs = cited_refs
        self.generate_calls = 0
        self.route_calls = 0
        self.verify_answer_calls = 0
        self.compose_calls = 0
        self.presentation_edit_calls = 0

    async def rewrite_query(self, question: str) -> str:
        raise AssertionError("Fast RAG 不应改写查询。")

    async def route_question(self, question: str) -> ResearchRouteDecision:
        self.route_calls += 1
        raise AssertionError("Fast RAG 不应调用模型路由。")

    async def generate_answer(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> AnswerDraft:
        self.generate_calls += 1
        assert question
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
        raise AssertionError("Fast RAG 不应规划子问题。")

    async def decide_research_action(
        self,
        *,
        question: str,
        available_queries: Sequence[str],
        observations: Sequence[dict[str, object]],
        tool_calls_remaining: int,
    ) -> ResearchToolAction:
        raise AssertionError("Fast RAG 不应进入复杂研究控制器。")

    async def verify_evidence(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> EvidenceVerification:
        raise AssertionError("Fast RAG 不应调用证据核验器。")

    async def verify_answer_claims(
        self,
        *,
        question: str,
        answer: str,
        evidences: Sequence[RetrievedEvidence],
        cited_refs: Sequence[str],
    ) -> AnswerClaimVerification:
        self.verify_answer_calls += 1
        raise AssertionError("Fast RAG 不应调用回答主张核验器。")

    async def compose_final_answer(
        self,
        *,
        question: str,
        draft_answer: str,
        verification: AnswerClaimVerification,
        evidences: Sequence[RetrievedEvidence],
    ) -> FinalAnswerDraft:
        self.compose_calls += 1
        raise AssertionError("Fast RAG 不应调用修复 composer。")

    async def edit_answer_presentation(
        self,
        *,
        question: str,
        supported_claims: Sequence[AnswerClaimVerificationItem],
        allowed_refs: Sequence[str],
    ) -> PresentationAnswerDraft:
        self.presentation_edit_calls += 1
        raise AssertionError("Fast RAG 不应调用展示编辑器。")


def _fast_decision():
    return resolve_research_execution_mode("该方法的实验结果是什么？", ResearchQuestionMode.FAST)


@pytest.mark.asyncio
async def test_fast_rag_renders_citations_without_strict_model_calls() -> None:
    """Fast RAG 成功时只调用 Writer，并按首次出现顺序渲染用户引用。"""
    stages: list[ResearchRunStage] = []

    async def record_stage(
        stage: ResearchRunStage, message: str | None, evidence_count: int
    ) -> None:
        assert message
        assert evidence_count >= 0
        stages.append(stage)

    retriever = FakeRetriever(
        RetrievalResult(
            evidences=(_evidence(), _second_evidence()),
            trace={
                "final_evidence_count": 2,
                "reranker": {"enabled": False, "status": "disabled"},
            },
        )
    )
    model = FastOnlyModel(
        answer_text="第二段先被引用【E2】，第一段随后被引用【E1】。",
        cited_refs=("E2", "E1"),
    )

    outcome = await FastRagRunner(
        retriever=retriever,
        model=model,
        settings=ResearchSettings(),
        stage_callback=record_stage,
    ).run(_context("该方法的实验结果是什么？"), _fast_decision())

    assert outcome.status is ResearchRunStatus.COMPLETED
    assert outcome.mode == "single_rag"
    assert outcome.answer == "第二段先被引用[1]，第一段随后被引用[2]。"
    assert outcome.cited_chunk_ids == (_SECOND_CHUNK_ID, _CHUNK_ID)
    assert retriever.queries == ["该方法的实验结果是什么？"]
    assert model.generate_calls == 1
    assert model.route_calls == 0
    assert model.verify_answer_calls == 0
    assert model.compose_calls == 0
    assert model.presentation_edit_calls == 0
    assert ResearchRunStage.EVIDENCE_VERIFYING not in stages
    assert outcome.retrieval_trace["mode"] == "fast_rag"
    assert outcome.retrieval_trace["execution_mode"] == "fast_rag"
    assert outcome.retrieval_trace["citation_checked"] is True
    assert outcome.retrieval_trace["claim_verified"] is False
    assert outcome.retrieval_trace["answer_claim_verification"] == {
        "status": "skipped",
        "reason": "fast_rag",
    }
    assert outcome.retrieval_trace["budget"] == {
        "model_calls": 1,
        "model_call_limit": ResearchSettings().rag_max_model_calls_per_run,
        "tool_calls": 1,
        "tool_call_limit": ResearchSettings().rag_max_react_tool_calls,
    }
    assert outcome.retrieval_trace["user_citations"] == [
        {"display_index": 1, "evidence_ref": "E2", "chunk_id": str(_SECOND_CHUNK_ID)},
        {"display_index": 2, "evidence_ref": "E1", "chunk_id": str(_CHUNK_ID)},
    ]


@pytest.mark.asyncio
async def test_fast_rag_empty_retrieval_does_not_upgrade_to_strict() -> None:
    """无证据时直接返回澄清和升档建议，不调用任何模型。"""
    retriever = FakeRetriever(RetrievalResult(evidences=(), trace={"final_evidence_count": 0}))
    model = FastOnlyModel()

    outcome = await FastRagRunner(
        retriever=retriever,
        model=model,
        settings=ResearchSettings(),
    ).run(_context("没有足够上下文的问题"), _fast_decision())

    assert outcome.status is ResearchRunStatus.AWAITING_CLARIFICATION
    assert outcome.cited_chunk_ids == ()
    assert "深度研究" in outcome.answer
    assert model.generate_calls == 0
    assert model.route_calls == 0
    assert outcome.retrieval_trace["outcome"] == "insufficient_evidence"
    assert outcome.retrieval_trace["suggested_next_mode"] == "strict_research"
    assert outcome.retrieval_trace["citation_checked"] is False
    assert outcome.retrieval_trace["claim_verified"] is False


@pytest.mark.asyncio
async def test_fast_rag_writer_insufficient_answer_does_not_repair() -> None:
    """Writer 声明证据不足时也不自动进入严格 verifier/repair 链路。"""
    retriever = FakeRetriever(
        RetrievalResult(evidences=(_evidence(),), trace={"final_evidence_count": 1})
    )
    model = FastOnlyModel(sufficient=False)

    outcome = await FastRagRunner(
        retriever=retriever,
        model=model,
        settings=ResearchSettings(),
    ).run(_context("该方法是否降低成本？"), _fast_decision())

    assert outcome.status is ResearchRunStatus.AWAITING_CLARIFICATION
    assert outcome.answer == "请限定实验条件。"
    assert model.generate_calls == 1
    assert model.verify_answer_calls == 0
    assert model.compose_calls == 0
    assert outcome.retrieval_trace["outcome"] == "answer_evidence_insufficient"
    assert outcome.retrieval_trace["suggested_next_mode"] == "strict_research"


@pytest.mark.asyncio
async def test_fast_rag_rejects_prose_and_structured_citation_mismatch() -> None:
    """Fast RAG 仍要求正文 EvidenceRef 与结构化 cited_refs 完全一致。"""
    retriever = FakeRetriever(
        RetrievalResult(
            evidences=(_evidence(), _second_evidence()),
            trace={"final_evidence_count": 2},
        )
    )
    model = FastOnlyModel(answer_text="第二段被引用【E2】。", cited_refs=("E1",))

    with pytest.raises(ResearchModelProtocolError, match="正文引用无效"):
        await FastRagRunner(
            retriever=retriever,
            model=model,
            settings=ResearchSettings(),
        ).run(_context("请按证据回答。"), _fast_decision())

    assert model.generate_calls == 1
    assert model.verify_answer_calls == 0
    assert model.compose_calls == 0
