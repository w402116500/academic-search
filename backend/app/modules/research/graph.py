"""以 LangGraph 组织受集合边界约束的单轮和复杂研究回答。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, TypedDict, cast
from uuid import UUID

from app.modules.research.contracts import ResearchRunMode, ResearchRunStage, ResearchRunStatus
from app.modules.research.execution import ResearchExecutionContext
from app.modules.research.retrieval import RetrievalResult, RetrievedEvidence
from app.modules.research.settings import ResearchSettings
from app.modules.workflow.settings import WorkflowSettings
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables.config import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError, model_validator


class ResearchModelError(RuntimeError):
    """研究图调用聊天模型或解析其结构化输出失败时抛出。"""


class ResearchRunCancelled(RuntimeError):
    """Worker 在可控边界发现持久化取消请求后停止图执行。"""


class ResearchBudgetExhausted(RuntimeError):
    """每次运行达到调用预算时中断后续工具或模型调用。"""


class ResearchRouteDecision(BaseModel):
    """结构化判定问题应走单轮还是跨论文研究模式。"""

    mode: Literal["single_rag", "multi_agent"]
    reason: str = Field(min_length=1, max_length=300)

    @model_validator(mode="before")
    @classmethod
    def normalize_router_alias(cls, value: object) -> object:
        """规范化两个已观察到的路由别名，不放宽可选模式集合。"""
        if not isinstance(value, dict) or "mode" in value:
            return value
        aliases = [key for key in ("router", "choice", "agent", "route") if key in value]
        if len(aliases) != 1:
            return value
        return {**value, "mode": value[aliases[0]]}


class ResearchToolAction(BaseModel):
    """复杂研究循环允许的下一步动作，不暴露模型内部推理。"""

    action: Literal["retrieve", "answer", "clarify"]
    query: str | None = Field(default=None, max_length=2_000)
    reason: str = Field(min_length=1, max_length=300)
    clarification_question: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def action_has_required_fields(self) -> ResearchToolAction:
        """检索只能从规划器给出的剩余子问题中选择一条明确查询。"""
        if self.action == "retrieve" and not self.query:
            raise ValueError("检索动作必须提供查询")
        if self.action == "clarify" and not self.clarification_question:
            raise ValueError("澄清动作必须提供澄清问题")
        return self


@dataclass(slots=True)
class ResearchRunBudget:
    """仅记录真实模型与检索调用次数，避免伪造货币成本。"""

    model_call_limit: int
    tool_call_limit: int
    model_calls: int = 0
    tool_calls: int = 0

    def consume_model_call(self) -> None:
        if self.model_calls >= self.model_call_limit:
            raise ResearchBudgetExhausted("已达到本次研究的模型调用预算。")
        self.model_calls += 1

    def consume_tool_call(self) -> None:
        if self.tool_calls >= self.tool_call_limit:
            raise ResearchBudgetExhausted("已达到本次研究的检索工具调用预算。")
        self.tool_calls += 1

    def snapshot(self) -> dict[str, object]:
        return {
            "model_calls": self.model_calls,
            "model_call_limit": self.model_call_limit,
            "tool_calls": self.tool_calls,
            "tool_call_limit": self.tool_call_limit,
        }


class QueryRewrite(BaseModel):
    """一次查询改写的受限输出；它不是回答事实或可引用内容。"""

    query: str = Field(min_length=1, max_length=2_000)


class AnswerDraft(BaseModel):
    """模型根据已给证据生成的回答草稿。"""

    answer: str = Field(min_length=1, max_length=12_000)
    cited_chunk_ids: list[UUID] = Field(default_factory=list, max_length=12)
    evidence_sufficient: bool
    clarification_question: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def sufficient_answer_must_cite_evidence(self) -> AnswerDraft:
        """证据不足允许空引用；声称证据充分时必须回链至少一个本次候选片段。"""
        if self.evidence_sufficient and not self.cited_chunk_ids:
            raise ValueError("证据充分的回答必须至少引用一个原文片段")
        return self


class SubquestionPlan(BaseModel):
    """复杂问题可执行的有限子问题计划。"""

    subquestions: list[str] = Field(min_length=2, max_length=8)


class EvidenceVerification(BaseModel):
    """证据核验器只能接受或拒绝候选片段，不能新造结论。"""

    supported_chunk_ids: list[UUID] = Field(default_factory=list, max_length=20)
    unresolved_aspects: list[str] = Field(default_factory=list, max_length=8)


class AnswerClaimVerificationItem(BaseModel):
    """回答中一个原子主张与实际引用片段之间的独立核验结果。"""

    claim: str = Field(min_length=1, max_length=1_500)
    supported: bool
    supporting_chunk_ids: list[UUID] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def supporting_chunks_match_verdict(self) -> AnswerClaimVerificationItem:
        """不允许核验器把无出处的主张标为支持，或为拒绝主张伪造来源。"""
        if self.supported and not self.supporting_chunk_ids:
            raise ValueError("受支持的回答主张必须关联至少一个引用片段")
        if not self.supported and self.supporting_chunk_ids:
            raise ValueError("不受支持的回答主张不能携带支持片段")
        return self


class AnswerClaimVerification(BaseModel):
    """独立核验器覆盖回答内全部事实性原子主张的结构化输出。"""

    claims: list[AnswerClaimVerificationItem] = Field(min_length=1, max_length=24)


class ResearchChatModel(Protocol):
    """研究图依赖的结构化模型边界，测试可替换为无需网络的实现。"""

    async def rewrite_query(self, question: str) -> str:
        """生成一次检索用改写，不返回研究结论。"""
        raise NotImplementedError

    async def route_question(self, question: str) -> ResearchRouteDecision:
        """根据问题结构选择单轮或跨论文研究模式，并给出用户可理解的依据。"""
        raise NotImplementedError

    async def generate_answer(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> AnswerDraft:
        """只根据输入证据生成回答，并返回实际采用的片段 UUID。"""
        raise NotImplementedError

    async def plan_subquestions(self, question: str, max_subquestions: int) -> tuple[str, ...]:
        """把复杂问题拆成有限、可检索的子问题。"""
        raise NotImplementedError

    async def decide_research_action(
        self,
        *,
        question: str,
        available_queries: Sequence[str],
        observations: Sequence[dict[str, object]],
        tool_calls_remaining: int,
    ) -> ResearchToolAction:
        """基于已观察到的检索摘要选择继续检索、回答或请求澄清。"""
        raise NotImplementedError

    async def verify_evidence(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> EvidenceVerification:
        """检查候选片段是否足以支撑当前问题，不能用外部常识补全。"""
        raise NotImplementedError

    async def verify_answer_claims(
        self,
        *,
        question: str,
        answer: str,
        evidences: Sequence[RetrievedEvidence],
    ) -> AnswerClaimVerification:
        """核验回答中每个事实性主张是否由其实际引用片段直接支持。"""
        raise NotImplementedError


class OpenAICompatibleResearchModel:
    """通过 LangChain 调用 DeepSeek 或其他 OpenAI 兼容聊天模型。"""

    def __init__(self, settings: WorkflowSettings, research_settings: ResearchSettings) -> None:
        self._settings = settings
        self._research_settings = research_settings
        self._client = ChatOpenAI(
            model=settings.active_chat_model,
            api_key=settings.active_api_key,
            base_url=settings.active_base_url,
            temperature=0,
            timeout=research_settings.rag_chat_timeout_seconds,
            max_retries=0,
        )

    async def rewrite_query(self, question: str) -> str:
        """严格输出单条改写查询，避免改写模型借机给出未经验证的答案。"""
        result = await self._invoke_structured(
            QueryRewrite,
            system=(
                "你只负责把研究问题改写为更适合学术全文检索的一条查询。"
                "不要回答问题、不要引入事实、不要生成多个查询。"
            ),
            human=question,
        )
        return result.query.strip()

    async def route_question(self, question: str) -> ResearchRouteDecision:
        """只按问题是否需要跨论文、多方面综合做结构化路由，不输出结论。"""
        return await self._invoke_structured(
            ResearchRouteDecision,
            system=(
                "你是文献研究路由器。只有问题明确要求比较、综合多篇论文、处理冲突证据或"
                "分别核验多个相互依赖的方面时，才选择 multi_agent；其余选择 single_rag。"
                "reason 必须是面向用户的简短理由，不得包含模型内部推理或研究结论。"
            ),
            human=question,
        )

    async def generate_answer(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> AnswerDraft:
        """要求回答正文显式标记证据编号，且引用 ID 只能来自输入片段。"""
        evidence_text = _evidence_prompt(evidences)
        result = await self._invoke_structured(
            AnswerDraft,
            system=(
                "你是严谨的文献研究助手。只能依据给定的论文原文证据回答，不能使用"
                "训练知识补全。正文中每个事实性结论后必须以【E序号】标注来源。"
                "如果证据不足，evidence_sufficient=false，answer 只说明不足，"
                "clarification_question 给出一个可帮助检索的追问。"
                "cited_chunk_ids 只能填写输入证据中真正支持回答的 chunk_id。\n\n"
                f"可用证据：\n{evidence_text}"
            ),
            human=question,
        )
        allowed_ids = {evidence.chunk_id for evidence in evidences}
        if not set(result.cited_chunk_ids).issubset(allowed_ids):
            raise ResearchModelError("回答模型返回了不属于当前研究集合的引用标识。")
        return result

    async def plan_subquestions(self, question: str, max_subquestions: int) -> tuple[str, ...]:
        """将复杂问题拆成有限的可验证子问题，禁止在计划节点给出结论。"""
        result = await self._invoke_structured(
            SubquestionPlan,
            system=(
                "你是文献研究规划器。把复杂问题拆成 2 到 "
                f"{max_subquestions} 个可以独立从论文原文检索和核验的子问题。"
                "不要回答原问题，不要生成超出论文集合范围的任务。"
            ),
            human=question,
        )
        normalized = tuple(" ".join(item.split()) for item in result.subquestions if item.strip())
        if not 2 <= len(normalized) <= max_subquestions or len(set(normalized)) != len(normalized):
            raise ResearchModelError("复杂问题规划没有生成有效且互不重复的子问题。")
        return normalized

    async def decide_research_action(
        self,
        *,
        question: str,
        available_queries: Sequence[str],
        observations: Sequence[dict[str, object]],
        tool_calls_remaining: int,
    ) -> ResearchToolAction:
        """依据公开的工具观察决定受限循环下一步，不允许调用集合外工具。"""
        result = await self._invoke_structured(
            ResearchToolAction,
            system=(
                "你是受限文献研究控制器。只能选择 retrieve、answer 或 clarify。"
                "retrieve 时 query 必须逐字从“可用子问题”列表中选择一条，且只能在剩余"
                "检索预算大于 0 时使用。answer 仅表示已有观察足以进入后续证据核验，不得"
                "生成答案。clarify 用于当前集合证据不足，并提供面向用户的追问。"
                "reason 只说明动作依据，不得包含内部推理。\n\n"
                f"可用子问题：{json.dumps(list(available_queries), ensure_ascii=False)}\n"
                f"已观察摘要：{json.dumps(list(observations), ensure_ascii=False)}\n"
                f"剩余检索次数：{tool_calls_remaining}"
            ),
            human=question,
        )
        if result.action == "retrieve" and result.query not in available_queries:
            raise ResearchModelError("复杂研究控制器选择了规划范围外的检索查询。")
        return result

    async def verify_evidence(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> EvidenceVerification:
        """仅评估原文片段与问题的支撑关系，输出可继续用于综合的证据 ID。"""
        result = await self._invoke_structured(
            EvidenceVerification,
            system=(
                "你是证据核验器。只保留能够直接支撑当前问题中一个具体方面的原文片段。"
                "不能把主题相关误判为结论支持，不能依据常识补充。\n\n"
                f"候选证据：\n{_evidence_prompt(evidences)}"
            ),
            human=question,
        )
        allowed_ids = {evidence.chunk_id for evidence in evidences}
        if not set(result.supported_chunk_ids).issubset(allowed_ids):
            raise ResearchModelError("证据核验器返回了未授权片段标识。")
        return result

    async def verify_answer_claims(
        self,
        *,
        question: str,
        answer: str,
        evidences: Sequence[RetrievedEvidence],
    ) -> AnswerClaimVerification:
        """以独立调用审查回答文本，不允许回答模型自行宣称引用足够。"""
        result = await self._invoke_structured(
            AnswerClaimVerification,
            system=(
                "你是独立的学术回答主张核验器。只根据给定的实际引用片段检查回答。"
                "必须列出回答中的全部事实性原子主张；claim 必须逐字连续出现在回答中。"
                "只有片段直接支持该主张时 supported 才能为 true，且 supporting_chunk_ids "
                "只能填写真正支持该主张的输入 chunk_id。不能依靠外部知识、主题相近或"
                "回答中的引用标号来补足证据。任何未被支持、扩大解释或无法定位的主张"
                "都必须标为 false 且 supporting_chunk_ids 为空。\n\n"
                f"实际引用片段：\n{_evidence_prompt(evidences)}"
            ),
            human=f"研究问题：{question}\n\n待核验回答：\n{answer}",
        )
        allowed_ids = {evidence.chunk_id for evidence in evidences}
        for item in result.claims:
            if item.claim not in answer:
                raise ResearchModelError("回答主张核验器返回了不属于回答正文的主张。")
            if not set(item.supporting_chunk_ids).issubset(allowed_ids):
                raise ResearchModelError("回答主张核验器返回了未实际引用的片段标识。")
        return result

    async def _invoke_structured(
        self,
        schema: type[BaseModel],
        *,
        system: str,
        human: str,
    ) -> Any:
        """统一转换兼容网关的调用和结构错误，避免把原始异常泄露到前端。"""
        try:
            model = self._client.with_structured_output(schema, method="json_mode")
            # DeepSeek 的 JSON mode 明确要求提示词包含 JSON；集中追加可保证
            # 改写、规划、核验与回答四类结构化调用拥有同一兼容性约束。
            json_system = f"{system}\n\n只返回符合字段约束的有效 JSON 对象，不要输出其他内容。"
            return await model.ainvoke([SystemMessage(json_system), HumanMessage(human)])
        except (ValidationError, ValueError) as exc:
            raise ResearchModelError("研究模型返回了不符合结构约束的结果。") from exc
        except Exception as exc:
            raise ResearchModelError("研究聊天模型暂时不可用。") from exc


class SingleRagState(TypedDict):
    """单轮图持久化到 LangGraph checkpoint 的 JSON 兼容状态。"""

    question: str
    query: str
    rewrite_count: int
    evidences: list[dict[str, object]]
    retrieval_trace: dict[str, object]
    route: Literal["answer", "rewrite", "clarify"]
    answer: str
    cited_chunk_ids: list[str]
    clarification_question: str


@dataclass(frozen=True, slots=True)
class ResearchGraphOutcome:
    """图执行结束后交给持久化服务的无 ORM 输出。"""

    status: ResearchRunStatus
    stage: ResearchRunStage
    answer: str
    evidences: tuple[RetrievedEvidence, ...]
    cited_chunk_ids: tuple[UUID, ...]
    retrieval_trace: dict[str, Any]
    mode: str


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
        checkpoint_database_url: str | None,
        stage_callback: StageCallback | None = None,
        cancellation_checker: CancellationChecker | None = None,
    ) -> None:
        self._retriever = retriever
        self._model = model
        self._settings = settings
        self._checkpoint_database_url = checkpoint_database_url
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
        graph = StateGraph(SingleRagState)
        graph.add_node("retrieve", cast(Any, self._single_retrieve(context)))
        graph.add_node("assess", self._single_assess)
        graph.add_node("rewrite", self._single_rewrite)
        graph.add_node("answer", self._single_answer)
        graph.add_node("verify_answer", self._single_verify_answer)
        graph.add_node("clarify", self._single_clarify)
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
        final_state = await self._invoke_with_checkpoint(
            graph, initial_state, context.langgraph_thread_id
        )
        evidences = tuple(_evidence_from_state(item) for item in final_state.get("evidences", []))
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

    def _single_retrieve(
        self, context: ResearchExecutionContext
    ) -> Callable[[SingleRagState], Awaitable[dict[str, object]]]:
        """创建绑定不可变集合 scope 的图节点，模型不会接触权限过滤参数。"""

        async def retrieve(state: SingleRagState) -> dict[str, object]:
            await self._emit(ResearchRunStage.HYBRID_RETRIEVAL, "正在检索当前集合中的原文证据。", 0)
            result: RetrievalResult = await self._retrieve(
                scope=context.retrieval_scope, query=state["query"]
            )
            await self._emit(
                ResearchRunStage.PARENT_MERGING,
                "正在补全同一论文中的相关上下文。",
                len(result.evidences),
            )
            return {
                "evidences": [_evidence_to_state(item) for item in result.evidences],
                "retrieval_trace": dict(result.trace),
            }

        return retrieve

    async def _single_assess(self, state: SingleRagState) -> dict[str, object]:
        """证据为空时最多允许一次改写，之后强制澄清而非循环猜测。"""
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

    async def _single_rewrite(self, state: SingleRagState) -> dict[str, object]:
        """查询改写只更新下一次检索词，不能作为答案或引用进入最终结果。"""
        rewritten = await self._call_model(lambda: self._model.rewrite_query(state["question"]))
        return {"query": rewritten, "rewrite_count": state["rewrite_count"] + 1}

    async def _single_answer(self, state: SingleRagState) -> dict[str, object]:
        """只把 RRF 入选证据传给回答模型，并拒绝模型越界返回的 UUID。"""
        evidences = tuple(_evidence_from_state(item) for item in state["evidences"])
        await self._emit(ResearchRunStage.ANSWERING, "正在依据已检索证据整理回答。", len(evidences))
        answer = await self._call_model(
            lambda: self._model.generate_answer(question=state["question"], evidences=evidences)
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

    async def _single_verify_answer(self, state: SingleRagState) -> dict[str, object]:
        """在保存单轮回答前独立核验其原子主张与实际引用的原文片段。"""
        evidences = tuple(_evidence_from_state(item) for item in state["evidences"])
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

    async def _single_clarify(self, state: SingleRagState) -> dict[str, object]:
        """证据不足是正常终态，前端会把澄清问题显示为助手消息。"""
        await self._emit(
            ResearchRunStage.AWAITING_CLARIFICATION,
            "当前集合证据不足，需要补充问题。",
            0,
        )
        return {
            "clarification_question": state.get("clarification_question")
            or "当前研究集合没有足够证据支持这个问题。请补充研究对象、条件或限定到具体论文。"
        }

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

    async def _invoke_with_checkpoint(
        self,
        graph: StateGraph,
        initial_state: SingleRagState,
        thread_id: str,
    ) -> SingleRagState:
        """在配置可用时使用 PostgreSQL checkpointer；测试可显式关闭以隔离图逻辑。"""
        config = cast(RunnableConfig, {"configurable": {"thread_id": thread_id}})
        if self._checkpoint_database_url is None:
            compiled = graph.compile()
            return cast(SingleRagState, await compiled.ainvoke(initial_state, config=config))
        async with AsyncPostgresSaver.from_conn_string(
            self._checkpoint_database_url
        ) as checkpointer:
            await checkpointer.setup()
            compiled = graph.compile(checkpointer=checkpointer)
            return cast(SingleRagState, await compiled.ainvoke(initial_state, config=config))

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


def _evidence_prompt(evidences: Sequence[RetrievedEvidence]) -> str:
    """仅把最小必要定位和原文提供给模型，避免将未授权元数据混入提示词。"""
    return "\n\n".join(
        (
            f"[E{index}] chunk_id={evidence.chunk_id}\n"
            f"论文：{evidence.title}（{evidence.publication_year or '年份未知'}）\n"
            f"定位：第 {evidence.page_start or '?'}-{evidence.page_end or '?'} 页；"
            f"章节：{' / '.join(evidence.section_path) or '未识别'}\n"
            f"原文：{evidence.content}"
        )
        for index, evidence in enumerate(evidences, start=1)
    )


def _evidence_to_state(evidence: RetrievedEvidence) -> dict[str, object]:
    """将 dataclass 转为 PostgreSQL checkpoint 可序列化的原始字典。"""
    return {
        "chunk_id": str(evidence.chunk_id),
        "document_id": str(evidence.document_id),
        "ingestion_run_id": str(evidence.ingestion_run_id),
        "paper_id": str(evidence.paper_id),
        "content": evidence.content,
        "page_start": evidence.page_start,
        "page_end": evidence.page_end,
        "section_path": list(evidence.section_path),
        "locator": evidence.locator,
        "title": evidence.title,
        "authors": list(evidence.authors),
        "publication_year": evidence.publication_year,
        "source_url": evidence.source_url,
        "vector_score": evidence.vector_score,
        "lexical_score": evidence.lexical_score,
        "rrf_score": evidence.rrf_score,
        "rerank_score": evidence.rerank_score,
        "rank": evidence.rank,
        "source_chunk_ids": [str(item) for item in evidence.source_chunk_ids],
        "parent_merged": evidence.parent_merged,
    }


def _evidence_from_state(data: dict[str, object]) -> RetrievedEvidence:
    """从 checkpoint 状态恢复强类型证据，非法状态会在 Worker 中明确失败。"""
    payload = cast(dict[str, Any], data)
    authors = payload.get("authors", [])
    locator = payload.get("locator", {})
    return RetrievedEvidence(
        chunk_id=UUID(str(payload["chunk_id"])),
        document_id=UUID(str(payload["document_id"])),
        ingestion_run_id=UUID(str(payload["ingestion_run_id"])),
        paper_id=UUID(str(payload["paper_id"])),
        content=str(payload["content"]),
        page_start=int(payload["page_start"]) if payload.get("page_start") is not None else None,
        page_end=int(payload["page_end"]) if payload.get("page_end") is not None else None,
        section_path=tuple(str(item) for item in payload.get("section_path", [])),
        locator=dict(locator) if isinstance(locator, dict) else {},
        title=str(payload["title"]),
        authors=tuple(dict(item) for item in authors if isinstance(item, dict)),
        publication_year=(
            int(payload["publication_year"])
            if payload.get("publication_year") is not None
            else None
        ),
        source_url=str(payload["source_url"]) if payload.get("source_url") is not None else None,
        vector_score=(
            float(payload["vector_score"]) if payload.get("vector_score") is not None else None
        ),
        lexical_score=(
            float(payload["lexical_score"]) if payload.get("lexical_score") is not None else None
        ),
        rrf_score=float(payload["rrf_score"]) if payload.get("rrf_score") is not None else None,
        rerank_score=(
            float(payload["rerank_score"]) if payload.get("rerank_score") is not None else None
        ),
        rank=int(payload["rank"]) if payload.get("rank") is not None else None,
        source_chunk_ids=tuple(UUID(str(item)) for item in payload.get("source_chunk_ids", [])),
        parent_merged=bool(payload.get("parent_merged", False)),
    )
