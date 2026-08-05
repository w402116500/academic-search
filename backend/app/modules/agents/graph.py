"""以 LangGraph 组织受集合边界约束的单轮和复杂研究回答。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from app.modules.agents.checkpoint import ResearchGraphExecutor
from app.modules.agents.contracts import (
    ResearchBudgetExhausted,
    ResearchChatModel,
    ResearchModelError,
    ResearchRunBudget,
    ResearchRunCancelled,
)
from app.modules.agents.nodes.single_rag import SingleRagNodes
from app.modules.agents.state import (
    ResearchGraphOutcome,
    SingleRagState,
    evidence_from_state,
)
from app.modules.rag.retrieval import RetrievalResult, RetrievedEvidence
from app.modules.research.contracts import ResearchRunMode, ResearchRunStage, ResearchRunStatus
from app.modules.research.execution_port import ResearchExecutionContext
from app.modules.research.settings import ResearchSettings

StageCallback = Callable[[ResearchRunStage, str | None, int], Awaitable[None]]
CancellationChecker = Callable[[], Awaitable[bool]]


class ResearchGraphRunner:
    """把受控检索、模型输出和 LangGraph checkpoint 组合为可恢复研究执行。"""

    def __init__(
        self,
        *,
        retriever: Any,
        model: ResearchChatModel,
        settings: ResearchSettings,
        graph_executor: ResearchGraphExecutor,
        stage_callback: StageCallback | None = None,
        cancellation_checker: CancellationChecker | None = None,
    ) -> None:
        self._retriever = retriever
        self._model = model
        self._settings = settings
        self._graph_executor = graph_executor
        self._stage_callback = stage_callback
        self._cancellation_checker = cancellation_checker
        self._budget: ResearchRunBudget | None = None

    async def run(self, context: ResearchExecutionContext) -> ResearchGraphOutcome:
        """按结构化路由选择受限流程，并在预算耗尽时安全请求澄清。"""
        self._budget = ResearchRunBudget(
            model_call_limit=self._settings.rag_max_model_calls_per_run,
            tool_call_limit=self._settings.rag_max_react_tool_calls,
        )
        await self._emit(ResearchRunStage.PREPARING, "正在判断问题需要的证据研究方式。", 0)
        try:
            decision = await self._call_model(lambda: self._model.route_question(context.question))
            routing = {
                "classifier": "structured_question_router",
                "mode": decision.mode,
                "reason": decision.reason,
            }
            if decision.mode == ResearchRunMode.MULTI_AGENT.value:
                return await self._run_complex(context, routing)
            return await self._run_single(context, routing)
        except ResearchBudgetExhausted as exc:
            return self._clarification_outcome(
                question=context.question,
                trace={
                    "routing": {
                        "classifier": "structured_question_router",
                        "outcome": "budget_exhausted",
                    },
                    "budget": self._budget.snapshot(),
                    "outcome": "budget_exhausted",
                    "budget_message": str(exc),
                },
                clarification="本次研究已达到可审计的调用预算，请缩小问题范围后重新提问。",
            )

    async def _run_single(
        self, context: ResearchExecutionContext, routing: dict[str, object]
    ) -> ResearchGraphOutcome:
        """执行可 checkpoint 的单轮 RAG 图，改写预算由状态和配置共同限制。"""
        nodes = SingleRagNodes(
            model=self._model,
            settings=self._settings,
            emit=self._emit,
            call_model=self._call_model,
            retrieve=lambda scope, query: self._retrieve(scope=scope, query=query),
        )
        graph = StateGraph(SingleRagState)
        graph.add_node("retrieve", cast(Any, nodes.retrieve(context)))
        graph.add_node("assess", nodes.assess)
        graph.add_node("rewrite", nodes.rewrite)
        graph.add_node("answer", nodes.answer)
        graph.add_node("verify_answer", nodes.verify_answer)
        graph.add_node("clarify", nodes.clarify)
        graph.add_edge(START, "retrieve")
        graph.add_edge("retrieve", "assess")
        graph.add_conditional_edges(
            "assess",
            lambda state: state["route"],
            {"answer": "answer", "rewrite": "rewrite", "clarify": "clarify"},
        )
        graph.add_edge("rewrite", "retrieve")
        graph.add_conditional_edges(
            "answer",
            lambda state: state["route"],
            {"answer": "verify_answer", "clarify": "clarify"},
        )
        graph.add_conditional_edges(
            "verify_answer",
            lambda state: state["route"],
            {"answer": END, "clarify": "clarify"},
        )
        graph.add_edge("clarify", END)
        initial_state: SingleRagState = {
            "question": context.question,
            "query": context.question,
            "rewrite_count": 0,
            "evidences": [],
            "retrieval_trace": {},
            "route": "clarify",
            "answer": "",
            "cited_chunk_ids": [],
            "clarification_question": "",
        }
        final_state = await self._graph_executor.invoke(
            graph, initial_state, context.langgraph_thread_id
        )
        evidences = tuple(evidence_from_state(item) for item in final_state.get("evidences", []))
        cited_chunk_ids = tuple(UUID(item) for item in final_state.get("cited_chunk_ids", []))
        route = final_state.get("route")
        if route == "answer":
            return ResearchGraphOutcome(
                status=ResearchRunStatus.COMPLETED,
                stage=ResearchRunStage.COMPLETED,
                answer=final_state["answer"],
                evidences=evidences,
                cited_chunk_ids=cited_chunk_ids,
                retrieval_trace=self._trace_with_governance(
                    final_state.get("retrieval_trace", {}),
                    routing=routing,
                    extra={
                        "rewrite_attempts": final_state.get("rewrite_count", 0),
                        "mode": ResearchRunMode.SINGLE_RAG.value,
                    },
                ),
                mode=ResearchRunMode.SINGLE_RAG.value,
            )
        clarification = final_state.get("clarification_question") or (
            "请补充希望核验的对象、条件或论文范围。"
        )
        return ResearchGraphOutcome(
            status=ResearchRunStatus.AWAITING_CLARIFICATION,
            stage=ResearchRunStage.AWAITING_CLARIFICATION,
            answer=clarification,
            evidences=evidences,
            cited_chunk_ids=(),
            retrieval_trace=self._trace_with_governance(
                final_state.get("retrieval_trace", {}),
                routing=routing,
                extra={
                    "rewrite_attempts": final_state.get("rewrite_count", 0),
                    "mode": ResearchRunMode.SINGLE_RAG.value,
                    "outcome": "clarification",
                },
            ),
            mode=ResearchRunMode.SINGLE_RAG.value,
        )

    async def _run_complex(
        self, context: ResearchExecutionContext, routing: dict[str, object]
    ) -> ResearchGraphOutcome:
        """用受限的“观察 -> 下一步”循环处理需要跨论文综合的问题。"""
        await self._emit(ResearchRunStage.PREPARING, "正在拆分需要分别核验的研究子问题。", 0)
        subquestions = await self._call_model(
            lambda: self._model.plan_subquestions(
                context.question, self._settings.rag_max_subquestions
            )
        )
        available_queries = list(subquestions)
        observations: list[dict[str, object]] = []
        react_steps: list[dict[str, object]] = []
        evidence_by_id: dict[UUID, RetrievedEvidence] = {}
        stopped_by_budget = False

        while True:
            budget = self._require_budget()
            if available_queries and budget.tool_calls >= budget.tool_call_limit:
                stopped_by_budget = True
                break
            tool_calls_remaining = budget.tool_call_limit - budget.tool_calls
            action = await self._call_model(
                lambda tool_calls_remaining=tool_calls_remaining: (
                    self._model.decide_research_action(
                        question=context.question,
                        available_queries=available_queries,
                        observations=observations,
                        tool_calls_remaining=tool_calls_remaining,
                    )
                )
            )
            step: dict[str, object] = {"action": action.action, "reason": action.reason}
            if action.action == "clarify":
                react_steps.append(step)
                return self._clarification_outcome(
                    question=context.question,
                    trace=self._complex_trace(
                        routing=routing,
                        subquestions=subquestions,
                        react_steps=react_steps,
                        extra={"outcome": "controller_requested_clarification"},
                    ),
                    clarification=action.clarification_question,
                )
            if action.action == "answer":
                react_steps.append(step)
                break

            if not available_queries:
                raise ResearchModelError("复杂研究控制器在无剩余子问题时仍请求检索。")
            query = cast(str, action.query)
            await self._emit(
                ResearchRunStage.HYBRID_RETRIEVAL,
                "正在检索下一个待核验的子问题。",
                len(evidence_by_id),
            )
            result = await self._retrieve(scope=context.retrieval_scope, query=query)
            observation = {
                "query": query,
                "evidence_count": len(result.evidences),
                "trace_summary": {
                    "vector_candidates": result.trace.get("vector_candidates", 0),
                    "keyword_candidates": result.trace.get("keyword_candidates", 0),
                    "final_evidence_count": result.trace.get("final_evidence_count", 0),
                },
            }
            observations.append(observation)
            react_steps.append({**step, "query": query, "observation": observation})
            available_queries.remove(query)
            for evidence in result.evidences:
                previous = evidence_by_id.get(evidence.chunk_id)
                if previous is None or (evidence.rrf_score or 0) > (previous.rrf_score or 0):
                    evidence_by_id[evidence.chunk_id] = evidence

        all_evidences = tuple(
            replace(evidence, rank=index)
            for index, evidence in enumerate(
                sorted(evidence_by_id.values(), key=lambda item: item.rrf_score or 0, reverse=True)[
                    : self._settings.rag_final_evidence_limit
                ],
                start=1,
            )
        )
        if not all_evidences:
            return self._clarification_outcome(
                question=context.question,
                trace=self._complex_trace(
                    routing=routing,
                    subquestions=subquestions,
                    react_steps=react_steps,
                    extra={
                        "outcome": "budget_exhausted" if stopped_by_budget else "no_knowledge",
                        "budget_exhausted": stopped_by_budget,
                    },
                ),
                clarification=(
                    "本次研究已达到检索预算，但当前集合没有找到足够证据。请缩小问题范围。"
                    if stopped_by_budget
                    else None
                ),
            )
        await self._emit(
            ResearchRunStage.EVIDENCE_VERIFYING,
            "正在核验原文是否支持跨论文结论。",
            len(all_evidences),
        )
        verification = await self._call_model(
            lambda: self._model.verify_evidence(question=context.question, evidences=all_evidences)
        )
        verified_ids = set(verification.supported_chunk_ids)
        verified = tuple(item for item in all_evidences if item.chunk_id in verified_ids)
        if not verified:
            return self._clarification_outcome(
                question=context.question,
                evidences=all_evidences,
                trace=self._complex_trace(
                    routing=routing,
                    subquestions=subquestions,
                    react_steps=react_steps,
                    extra={
                        "unresolved_aspects": verification.unresolved_aspects,
                        "outcome": "evidence_not_supported",
                        "budget_exhausted": stopped_by_budget,
                    },
                ),
            )
        await self._emit(ResearchRunStage.ANSWERING, "正在综合已核验的文献证据。", len(verified))
        answer = await self._call_model(
            lambda: self._model.generate_answer(question=context.question, evidences=verified)
        )
        if not answer.evidence_sufficient:
            return self._clarification_outcome(
                question=context.question,
                evidences=verified,
                trace=self._complex_trace(
                    routing=routing,
                    subquestions=subquestions,
                    react_steps=react_steps,
                    extra={
                        "unresolved_aspects": verification.unresolved_aspects,
                        "outcome": "answer_evidence_insufficient",
                        "budget_exhausted": stopped_by_budget,
                    },
                ),
                clarification=answer.clarification_question,
            )
        cited_ids = set(answer.cited_chunk_ids)
        cited_evidences = tuple(item for item in verified if item.chunk_id in cited_ids)
        if len(cited_evidences) != len(cited_ids):
            raise ResearchModelError("回答引用不属于本次已核验证据。")
        answer_verification = await self._call_model(
            lambda: self._model.verify_answer_claims(
                question=context.question,
                answer=answer.answer,
                evidences=cited_evidences,
            )
        )
        unsupported_count = sum(not item.supported for item in answer_verification.claims)
        trace = self._complex_trace(
            routing=routing,
            subquestions=subquestions,
            react_steps=react_steps,
            extra={
                "verified_evidence_count": len(verified),
                "unresolved_aspects": verification.unresolved_aspects,
                "budget_exhausted": stopped_by_budget,
                "answer_claim_verification": {
                    "claim_count": len(answer_verification.claims),
                    "unsupported_claim_count": unsupported_count,
                    "status": "supported" if unsupported_count == 0 else "unsupported",
                },
            },
        )
        if unsupported_count:
            return self._clarification_outcome(
                question=context.question,
                evidences=verified,
                trace={**trace, "outcome": "answer_claims_unsupported"},
                clarification="当前原文无法完整支持准备输出的结论。请缩小问题范围或补充相关文献。",
            )
        return ResearchGraphOutcome(
            status=ResearchRunStatus.COMPLETED,
            stage=ResearchRunStage.COMPLETED,
            answer=answer.answer,
            evidences=verified,
            cited_chunk_ids=tuple(answer.cited_chunk_ids),
            retrieval_trace=trace,
            mode=ResearchRunMode.MULTI_AGENT.value,
        )

    async def _call_model(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        """在每次外部模型调用的前后检查协作取消并计入真实调用预算。"""
        self._require_budget().consume_model_call()
        await self._ensure_not_cancelled()
        result = await operation()
        await self._ensure_not_cancelled()
        return result

    async def _retrieve(self, *, scope: object, query: str) -> RetrievalResult:
        """复杂与单轮流程共用当前集合范围内的唯一检索工具。"""
        self._require_budget().consume_tool_call()
        await self._ensure_not_cancelled()
        result = await self._retriever.retrieve(scope=scope, query=query)
        await self._ensure_not_cancelled()
        return result

    async def _ensure_not_cancelled(self) -> None:
        """外部调用无法强杀，但每个开始和返回边界都必须观察持久化取消请求。"""
        if self._cancellation_checker is not None and await self._cancellation_checker():
            raise ResearchRunCancelled("研究运行已收到取消请求。")

    def _require_budget(self) -> ResearchRunBudget:
        """每个 Runner 实例只执行一个运行，因此预算在图节点间可安全共享。"""
        if self._budget is None:
            raise RuntimeError("研究运行尚未初始化调用预算。")
        return self._budget

    def _trace_with_governance(
        self,
        trace: dict[str, Any],
        *,
        routing: dict[str, object],
        extra: dict[str, object],
    ) -> dict[str, Any]:
        """把可理解路由和实际资源消耗写入最终运行 trace。"""
        return {
            **trace,
            **extra,
            "routing": routing,
            "budget": self._require_budget().snapshot(),
        }

    def _complex_trace(
        self,
        *,
        routing: dict[str, object],
        subquestions: Sequence[str],
        react_steps: Sequence[dict[str, object]],
        extra: dict[str, object],
    ) -> dict[str, Any]:
        """仅记录动作、工具输入与公开观察，不保存模型内部推理。"""
        return self._trace_with_governance(
            {},
            routing=routing,
            extra={
                "mode": ResearchRunMode.MULTI_AGENT.value,
                "subquestions": list(subquestions),
                "react_steps": list(react_steps),
                "tool_calls": self._require_budget().tool_calls,
                **extra,
            },
        )

    async def _emit(
        self,
        stage: ResearchRunStage,
        message: str | None,
        evidence_count: int,
    ) -> None:
        """让 Worker 更新持久状态并发布 SSE 事件；纯图单测可不注入回调。"""
        await self._ensure_not_cancelled()
        if self._stage_callback is not None:
            await self._stage_callback(stage, message, evidence_count)
        await self._ensure_not_cancelled()

    @staticmethod
    def _clarification_outcome(
        *,
        question: str,
        evidences: tuple[RetrievedEvidence, ...] = (),
        trace: dict[str, Any],
        clarification: str | None = None,
    ) -> ResearchGraphOutcome:
        """复杂图的证据不足统一落为可见澄清终态，而不是返回模型记忆答案。"""
        return ResearchGraphOutcome(
            status=ResearchRunStatus.AWAITING_CLARIFICATION,
            stage=ResearchRunStage.AWAITING_CLARIFICATION,
            answer=clarification
            or f"当前集合没有足够的已核验证据支持“{question}”。请缩小问题范围或补充相关文献。",
            evidences=evidences,
            cited_chunk_ids=(),
            retrieval_trace=trace,
            mode=ResearchRunMode.MULTI_AGENT.value,
        )
