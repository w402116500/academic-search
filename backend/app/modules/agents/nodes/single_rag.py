"""单轮 RAG 图节点。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from app.modules.agents.contracts import ResearchChatModel, ResearchModelError
from app.modules.agents.state import SingleRagState, evidence_from_state, evidence_to_state
from app.modules.rag.retrieval import RetrievalResult
from app.modules.research.contracts import ResearchRunStage
from app.modules.research.execution_port import ResearchExecutionContext
from app.modules.research.settings import ResearchSettings

StageEmitter = Callable[[ResearchRunStage, str | None, int], Awaitable[None]]
ModelCaller = Callable[[Callable[[], Awaitable[Any]]], Awaitable[Any]]
RetrievalCaller = Callable[[object, str], Awaitable[RetrievalResult]]


class SingleRagNodes:
    """绑定单轮图所需的模型、检索和公开阶段回调。"""

    def __init__(
        self,
        *,
        model: ResearchChatModel,
        settings: ResearchSettings,
        emit: StageEmitter,
        call_model: ModelCaller,
        retrieve: RetrievalCaller,
    ) -> None:
        self._model = model
        self._settings = settings
        self._emit = emit
        self._call_model = call_model
        self._retrieve = retrieve

    def retrieve(
        self, context: ResearchExecutionContext
    ) -> Callable[[SingleRagState], Awaitable[dict[str, object]]]:
        """创建绑定不可变集合 scope 的节点，模型不会接触权限过滤参数。"""

        async def retrieve_node(state: SingleRagState) -> dict[str, object]:
            await self._emit(ResearchRunStage.HYBRID_RETRIEVAL, "正在检索当前集合中的原文证据。", 0)
            result = await self._retrieve(context.retrieval_scope, state["query"])
            await self._emit(
                ResearchRunStage.PARENT_MERGING,
                "正在补全同一论文中的相关上下文。",
                len(result.evidences),
            )
            return {
                "evidences": [evidence_to_state(item) for item in result.evidences],
                "retrieval_trace": dict(result.trace),
            }

        return retrieve_node

    async def assess(self, state: SingleRagState) -> dict[str, object]:
        """证据为空时最多允许一次改写，之后强制澄清。"""
        reranker = state["retrieval_trace"].get("reranker")
        reranker_enabled = isinstance(reranker, dict) and reranker.get("enabled") is True
        await self._emit(
            ResearchRunStage.RERANKING,
            "正在使用真实 Reranker 精排候选证据。"
            if reranker_enabled
            else "正在按 RRF 融合结果筛选证据；当前未启用真实 Reranker。",
            len(state["evidences"]),
        )
        if state["evidences"]:
            return {"route": "answer"}
        if state["rewrite_count"] < self._settings.rag_max_query_rewrites:
            return {"route": "rewrite"}
        return {"route": "clarify"}

    async def rewrite(self, state: SingleRagState) -> dict[str, object]:
        """查询改写只更新检索词，不能作为答案或引用进入最终结果。"""
        rewritten = await self._call_model(lambda: self._model.rewrite_query(state["question"]))
        return {"query": rewritten, "rewrite_count": state["rewrite_count"] + 1}

    async def answer(self, state: SingleRagState) -> dict[str, object]:
        """只把 RRF 入选证据传给回答模型。"""
        evidences = tuple(evidence_from_state(item) for item in state["evidences"])
        await self._emit(
            ResearchRunStage.ANSWERING,
            "正在依据已检索证据整理回答。",
            len(evidences),
        )
        answer = await self._call_model(
            lambda: self._model.generate_answer(
                question=state["question"],
                evidences=evidences,
            )
        )
        if not answer.evidence_sufficient:
            return {
                "route": "clarify",
                "clarification_question": answer.clarification_question
                or "当前证据不足以回答，请补充研究对象或限定条件。",
            }
        return {
            "route": "answer",
            "answer": answer.answer,
            "cited_chunk_ids": [str(item) for item in answer.cited_chunk_ids],
        }

    async def verify_answer(self, state: SingleRagState) -> dict[str, object]:
        """保存回答前独立核验原子主张与实际引用片段。"""
        evidences = tuple(evidence_from_state(item) for item in state["evidences"])
        cited_ids = {UUID(item) for item in state["cited_chunk_ids"]}
        cited_evidences = tuple(item for item in evidences if item.chunk_id in cited_ids)
        if len(cited_evidences) != len(cited_ids):
            raise ResearchModelError("回答引用不属于本次已检索证据。")
        await self._emit(
            ResearchRunStage.EVIDENCE_VERIFYING,
            "正在逐项核验回答主张是否被实际引用的原文支持。",
            len(cited_evidences),
        )
        verification = await self._call_model(
            lambda: self._model.verify_answer_claims(
                question=state["question"],
                answer=state["answer"],
                evidences=cited_evidences,
            )
        )
        unsupported_count = sum(not item.supported for item in verification.claims)
        trace = {
            **state["retrieval_trace"],
            "answer_claim_verification": {
                "claim_count": len(verification.claims),
                "unsupported_claim_count": unsupported_count,
                "status": "supported" if unsupported_count == 0 else "unsupported",
            },
        }
        if unsupported_count:
            return {
                "route": "clarify",
                "clarification_question": (
                    "当前检索到的原文无法完整支持准备输出的结论。请缩小问题范围或补充相关文献。"
                ),
                "retrieval_trace": trace,
            }
        return {"route": "answer", "retrieval_trace": trace}

    async def clarify(self, state: SingleRagState) -> dict[str, object]:
        """证据不足是正常终态，前端会显示澄清问题。"""
        await self._emit(
            ResearchRunStage.AWAITING_CLARIFICATION,
            "当前集合证据不足，需要补充问题。",
            0,
        )
        return {
            "clarification_question": state.get("clarification_question")
            or "当前研究集合没有足够证据支持这个问题。请补充研究对象、条件或限定到具体论文。"
        }
