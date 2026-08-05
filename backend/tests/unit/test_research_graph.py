"""LangGraph 研究回答的离线行为测试。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

import pytest
from app.modules.agents.checkpoint import DirectResearchGraphExecutor
from app.modules.agents.contracts import (
    AnswerClaimVerification,
    AnswerClaimVerificationItem,
    AnswerDraft,
    EvidenceVerification,
    ResearchRouteDecision,
    ResearchRunCancelled,
    ResearchToolAction,
)
from app.modules.agents.graph import ResearchGraphRunner
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

    def __init__(
        self,
        *,
        sufficient: bool = True,
        claims_supported: bool = True,
        route_mode: Literal["single_rag", "multi_agent"] = "single_rag",
    ) -> None:
        self.sufficient = sufficient
        self.claims_supported = claims_supported
        self.route_mode: Literal["single_rag", "multi_agent"] = route_mode
        self.rewrite_count = 0

    async def rewrite_query(self, question: str) -> str:
        self.rewrite_count += 1
        return f"rewritten: {question}"

    async def route_question(self, question: str) -> ResearchRouteDecision:
        return ResearchRouteDecision(
            mode=self.route_mode,
            reason="问题需要分别核验多个方面。"
            if self.route_mode == "multi_agent"
            else "问题可以由同一组原文证据直接核验。",
        )

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
        return EvidenceVerification(supported_chunk_ids=[item.chunk_id for item in evidences])

    async def verify_answer_claims(
        self,
        *,
        question: str,
        answer: str,
        evidences: Sequence[RetrievedEvidence],
    ) -> AnswerClaimVerification:
        assert question
        return AnswerClaimVerification(
            claims=[
                AnswerClaimVerificationItem(
                    claim=answer,
                    supported=self.claims_supported,
                    supporting_chunk_ids=[evidences[0].chunk_id] if self.claims_supported else [],
                )
            ]
        )


@pytest.mark.parametrize("alias", ["router", "choice", "agent", "route"])
def test_structured_router_accepts_known_model_aliases(alias: str) -> None:
    """真实 OpenAI 兼容模型的已知别名应归一到稳定 mode 契约。"""
    decision = ResearchRouteDecision.model_validate(
        {alias: "single_rag", "reason": "问题无需多源比较。"}
    )

    assert decision.mode == "single_rag"


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
        graph_executor=DirectResearchGraphExecutor(),
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
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("没有足够上下文的问题"))

    assert outcome.status is ResearchRunStatus.AWAITING_CLARIFICATION
    assert model.rewrite_count == 1
    assert len(retriever.queries) == 2
    assert outcome.cited_chunk_ids == ()


@pytest.mark.asyncio
async def test_single_rag_rejects_an_answer_with_an_unsupported_atomic_claim() -> None:
    """回答模型声称证据充分也必须经过独立主张核验，否则只返回证据不足。"""
    retriever = FakeRetriever([RetrievalResult(evidences=(_evidence(),), trace={"final": 1})])
    outcome = await ResearchGraphRunner(
        retriever=retriever,
        model=FakeModel(claims_supported=False),
        settings=ResearchSettings(),
        graph_executor=DirectResearchGraphExecutor(),
    ).run(_context("该方法的实验结果是什么？"))

    assert outcome.status is ResearchRunStatus.AWAITING_CLARIFICATION
    assert outcome.cited_chunk_ids == ()
    assert outcome.retrieval_trace["answer_claim_verification"] == {
        "claim_count": 1,
        "unsupported_claim_count": 1,
        "status": "unsupported",
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
