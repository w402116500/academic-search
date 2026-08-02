"""LangGraph 研究回答的离线行为测试。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import pytest
from app.modules.research.contracts import ResearchRunStatus
from app.modules.research.execution import ResearchExecutionContext
from app.modules.research.graph import (
    AnswerDraft,
    EvidenceVerification,
    ResearchGraphRunner,
)
from app.modules.research.retrieval import RetrievalResult, RetrievedEvidence
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

    def __init__(self, *, sufficient: bool = True) -> None:
        self.sufficient = sufficient
        self.rewrite_count = 0

    async def rewrite_query(self, question: str) -> str:
        self.rewrite_count += 1
        return f"rewritten: {question}"

    async def generate_answer(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> AnswerDraft:
        return AnswerDraft(
            answer="该结论由原文结果段支持。【E1】",
            cited_chunk_ids=[evidences[0].chunk_id],
            evidence_sufficient=self.sufficient,
            clarification_question="请限定实验条件。" if not self.sufficient else None,
        )

    async def plan_subquestions(self, question: str, max_subquestions: int) -> tuple[str, ...]:
        return ("方法差异是什么？", "结果差异是什么？")

    async def verify_evidence(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> EvidenceVerification:
        return EvidenceVerification(supported_chunk_ids=[item.chunk_id for item in evidences])


def test_answer_draft_allows_empty_citations_only_for_insufficient_evidence() -> None:
    """证据不足是可见正常终态，不能因空引用被误判为模型协议故障。"""
    clarification = AnswerDraft(
        answer="当前证据不足。",
        cited_chunk_ids=[],
        evidence_sufficient=False,
        clarification_question="请补充原文。",
    )
    assert clarification.cited_chunk_ids == []

    with pytest.raises(ValidationError, match="必须至少引用"):
        AnswerDraft(answer="结论成立。", cited_chunk_ids=[], evidence_sufficient=True)


@pytest.mark.asyncio
async def test_single_rag_only_uses_retrieved_evidence_for_citation() -> None:
    """单轮回答应完成并只引用 Retriever 返回的当前集合片段。"""
    retriever = FakeRetriever([RetrievalResult(evidences=(_evidence(),), trace={"final": 1})])
    outcome = await ResearchGraphRunner(
        retriever=retriever,
        model=FakeModel(),
        settings=ResearchSettings(),
        checkpoint_database_url=None,
    ).run(_context("该方法的实验结果是什么？"))

    assert outcome.status is ResearchRunStatus.COMPLETED
    assert outcome.cited_chunk_ids == (_CHUNK_ID,)
    assert outcome.evidences[0].page_start == 5
    assert retriever.queries == ["该方法的实验结果是什么？"]


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
        checkpoint_database_url=None,
    ).run(_context("没有足够上下文的问题"))

    assert outcome.status is ResearchRunStatus.AWAITING_CLARIFICATION
    assert model.rewrite_count == 1
    assert len(retriever.queries) == 2
    assert outcome.cited_chunk_ids == ()


@pytest.mark.asyncio
async def test_complex_question_uses_planning_and_evidence_verification() -> None:
    """包含比较意图的问题进入受限复杂模式，并在核验后才生成最终回答。"""
    retriever = FakeRetriever(
        [
            RetrievalResult(evidences=(_evidence(),), trace={"final": 1}),
            RetrievalResult(evidences=(_evidence(),), trace={"final": 1}),
        ]
    )
    outcome = await ResearchGraphRunner(
        retriever=retriever,
        model=FakeModel(),
        settings=ResearchSettings(),
        checkpoint_database_url=None,
    ).run(_context("请比较两篇论文的方法和结果差异。"))

    assert outcome.status is ResearchRunStatus.COMPLETED
    assert outcome.mode == "multi_agent"
    assert outcome.retrieval_trace["tool_calls"] == 2
    assert outcome.cited_chunk_ids == (_CHUNK_ID,)
