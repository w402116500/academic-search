"""Fast RAG path for default research chat questions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from app.modules.agents.contracts import (
    ResearchBudgetExhausted,
    ResearchChatModel,
    ResearchModelError,
    ResearchModelProtocolError,
    ResearchRunBudget,
    ResearchRunCancelled,
)
from app.modules.agents.evidence_refs import (
    canonical_answer_cited_refs,
    chunk_ids_for_refs,
    evidence_snapshot_trace,
    render_user_citations,
)
from app.modules.agents.state import ResearchGraphOutcome
from app.modules.rag.retrieval import RetrievalResult, RetrievedEvidence
from app.modules.research.contracts import ResearchRunMode, ResearchRunStage, ResearchRunStatus
from app.modules.research.execution_port import ResearchExecutionContext
from app.modules.research.question_mode import ResearchModeDecision
from app.modules.research.settings import ResearchSettings

StageCallback = Callable[[ResearchRunStage, str | None, int], Awaitable[None]]
CancellationChecker = Callable[[], Awaitable[bool]]


class FastRagRunner:
    """执行默认快速问答，只保留确定性引用校验与用户引用渲染。"""

    def __init__(
        self,
        *,
        retriever: Any,
        model: ResearchChatModel,
        settings: ResearchSettings,
        stage_callback: StageCallback | None = None,
        cancellation_checker: CancellationChecker | None = None,
    ) -> None:
        self._retriever = retriever
        self._model = model
        self._settings = settings
        self._stage_callback = stage_callback
        self._cancellation_checker = cancellation_checker
        self._budget: ResearchRunBudget | None = None

    async def run(
        self, context: ResearchExecutionContext, decision: ResearchModeDecision
    ) -> ResearchGraphOutcome:
        """Answer from one retrieval pass and one writer call, without strict verifier hops."""
        self._budget = ResearchRunBudget(
            model_call_limit=self._settings.rag_max_model_calls_per_run,
            tool_call_limit=self._settings.rag_max_react_tool_calls,
        )
        routing = decision.to_trace()
        await self._emit(ResearchRunStage.PREPARING, "正在使用快速问答模式准备检索。", 0)
        try:
            await self._emit(
                ResearchRunStage.HYBRID_RETRIEVAL,
                "正在检索当前集合中的原文证据。",
                0,
            )
            result = await self._retrieve(scope=context.retrieval_scope, query=context.question)
            await self._emit(
                ResearchRunStage.PARENT_MERGING,
                "正在补全同一论文中的相关上下文。",
                len(result.evidences),
            )
            await self._emit(
                ResearchRunStage.RERANKING,
                _reranking_message(result.trace),
                len(result.evidences),
            )
            if not result.evidences:
                return self._clarification_outcome(
                    question=context.question,
                    evidences=(),
                    retrieval_trace=result.trace,
                    routing=routing,
                )

            evidences = tuple(result.evidences)
            await self._emit(
                ResearchRunStage.ANSWERING,
                "正在依据入选证据快速整理回答。",
                len(evidences),
            )
            answer = await self._call_model_with_snapshot(
                lambda: self._model.generate_answer(
                    question=context.question,
                    evidences=evidences,
                ),
                evidences,
            )
            if not answer.evidence_sufficient:
                return self._clarification_outcome(
                    question=context.question,
                    evidences=evidences,
                    retrieval_trace={
                        **result.trace,
                        "evidence_snapshot": evidence_snapshot_trace(evidences),
                    },
                    routing=routing,
                    clarification=answer.clarification_question,
                    outcome="answer_evidence_insufficient",
                )
            cited_refs = self._validated_cited_refs_from_answer(
                answer=answer.answer,
                evidences=evidences,
                cited_refs=answer.cited_refs,
            )
            try:
                rendered_answer, citations = render_user_citations(answer.answer, evidences)
            except ValueError as exc:
                raise ResearchModelError("最终回答引用了不属于当前证据快照的标识。") from exc
            if not citations:
                raise ResearchModelError("最终回答没有可展示的证据引用。")
            rendered_refs = [citation.evidence_ref for citation in citations]
            return ResearchGraphOutcome(
                status=ResearchRunStatus.COMPLETED,
                stage=ResearchRunStage.COMPLETED,
                answer=rendered_answer,
                evidences=evidences,
                cited_chunk_ids=chunk_ids_for_refs(evidences, rendered_refs),
                retrieval_trace=self._fast_trace(
                    result.trace,
                    routing=routing,
                    citation_checked=True,
                    extra={
                        "evidence_snapshot": evidence_snapshot_trace(evidences),
                        "cited_refs": list(cited_refs),
                        "user_citations": [
                            {
                                "display_index": citation.display_index,
                                "evidence_ref": citation.evidence_ref,
                                "chunk_id": str(citation.chunk_id),
                            }
                            for citation in citations
                        ],
                    },
                ),
                mode=ResearchRunMode.SINGLE_RAG.value,
            )
        except ResearchBudgetExhausted as exc:
            return self._clarification_outcome(
                question=context.question,
                evidences=(),
                retrieval_trace={"outcome": "budget_exhausted", "budget_message": str(exc)},
                routing=routing,
                clarification="本次快速问答已达到调用预算，请缩小问题范围或切换深度研究。",
            )

    async def _call_model(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        """在 Writer 调用前后检查取消，并计入真实模型预算。"""
        self._require_budget().consume_model_call()
        await self._ensure_not_cancelled()
        result = await operation()
        await self._ensure_not_cancelled()
        return result

    async def _call_model_with_snapshot(
        self,
        operation: Callable[[], Awaitable[Any]],
        evidences: Sequence[RetrievedEvidence],
    ) -> Any:
        """Attach EvidenceSnapshot data when the writer violates the citation protocol."""
        try:
            return await self._call_model(operation)
        except ResearchModelProtocolError as exc:
            exc.add_evidence_snapshot(evidence_snapshot_trace(evidences))
            raise

    async def _retrieve(self, *, scope: object, query: str) -> RetrievalResult:
        """Run the single fast retrieval tool call under the normal run budget."""
        self._require_budget().consume_tool_call()
        await self._ensure_not_cancelled()
        result = await self._retriever.retrieve(scope=scope, query=query)
        await self._ensure_not_cancelled()
        return result

    async def _emit(
        self,
        stage: ResearchRunStage,
        message: str | None,
        evidence_count: int,
    ) -> None:
        """Forward public progress to the worker and observe cancellation boundaries."""
        await self._ensure_not_cancelled()
        if self._stage_callback is not None:
            await self._stage_callback(stage, message, evidence_count)
        await self._ensure_not_cancelled()

    async def _ensure_not_cancelled(self) -> None:
        """Stop at safe boundaries if the user has requested cancellation."""
        if self._cancellation_checker is not None and await self._cancellation_checker():
            raise ResearchRunCancelled("研究运行已收到取消请求。")

    def _require_budget(self) -> ResearchRunBudget:
        """Each runner instance owns one run budget."""
        if self._budget is None:
            raise RuntimeError("快速研究运行尚未初始化调用预算。")
        return self._budget

    def _validated_cited_refs_from_answer(
        self,
        *,
        answer: str,
        evidences: Sequence[RetrievedEvidence],
        cited_refs: Sequence[str],
    ) -> tuple[str, ...]:
        try:
            return canonical_answer_cited_refs(answer, evidences, cited_refs)
        except ValueError as exc:
            error = ResearchModelProtocolError("回答正文引用无效，无法映射到证据快照。")
            error.add_evidence_snapshot(evidence_snapshot_trace(evidences))
            raise error from exc

    def _fast_trace(
        self,
        trace: dict[str, object],
        *,
        routing: dict[str, object],
        citation_checked: bool,
        extra: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        """Write the fast/strict tradeoff explicitly into the public trace."""
        return {
            **trace,
            **(extra or {}),
            "mode": "fast_rag",
            "execution_mode": "fast_rag",
            "requested_mode": routing["requested_mode"],
            "routing": routing,
            "rewrite_attempts": 0,
            "citation_checked": citation_checked,
            "claim_verified": False,
            "answer_claim_verification": {"status": "skipped", "reason": "fast_rag"},
            "budget": self._require_budget().snapshot(),
        }

    def _clarification_outcome(
        self,
        *,
        question: str,
        evidences: tuple[RetrievedEvidence, ...],
        retrieval_trace: dict[str, object],
        routing: dict[str, object],
        clarification: str | None = None,
        outcome: str = "insufficient_evidence",
    ) -> ResearchGraphOutcome:
        """Fast RAG evidence gaps stay visible instead of silently upgrading to Strict."""
        return ResearchGraphOutcome(
            status=ResearchRunStatus.AWAITING_CLARIFICATION,
            stage=ResearchRunStage.AWAITING_CLARIFICATION,
            answer=clarification
            or f"当前集合没有足够证据支持“{question}”。可以缩小问题范围，或切换深度研究后重试。",
            evidences=evidences,
            cited_chunk_ids=(),
            retrieval_trace=self._fast_trace(
                retrieval_trace,
                routing=routing,
                citation_checked=False,
                extra={
                    "outcome": outcome,
                    "suggested_next_mode": "strict_research",
                    "evidence_snapshot": evidence_snapshot_trace(evidences),
                },
            ),
            mode=ResearchRunMode.SINGLE_RAG.value,
        )


def _reranking_message(trace: dict[str, object]) -> str:
    reranker = trace.get("reranker")
    reranker_enabled = isinstance(reranker, dict) and reranker.get("enabled") is True
    return (
        "正在使用真实 Reranker 精排候选证据。"
        if reranker_enabled
        else "正在按 RRF 融合结果筛选证据；当前未启用真实 Reranker。"
    )
