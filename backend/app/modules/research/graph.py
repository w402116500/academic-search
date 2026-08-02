"""以 LangGraph 组织受集合边界约束的单轮和复杂研究回答。"""

from __future__ import annotations

import asyncio
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


class ResearchChatModel(Protocol):
    """研究图依赖的结构化模型边界，测试可替换为无需网络的实现。"""

    async def rewrite_query(self, question: str) -> str:
        """生成一次检索用改写，不返回研究结论。"""
        raise NotImplementedError

    async def generate_answer(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> AnswerDraft:
        """只根据输入证据生成回答，并返回实际采用的片段 UUID。"""
        raise NotImplementedError

    async def plan_subquestions(self, question: str, max_subquestions: int) -> tuple[str, ...]:
        """把复杂问题拆成有限、可检索的子问题。"""
        raise NotImplementedError

    async def verify_evidence(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> EvidenceVerification:
        """检查候选片段是否足以支撑当前问题，不能用外部常识补全。"""
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


class ResearchGraphRunner:
    """把受控检索、模型输出和 LangGraph checkpoint 组合为可恢复研究执行。"""

    _COMPLEX_MARKERS = ("比较", "对比", "差异", "异同", "综述", "冲突", "多篇", "compare", "review")

    def __init__(
        self,
        *,
        retriever: Any,
        model: ResearchChatModel,
        settings: ResearchSettings,
        checkpoint_database_url: str | None,
        stage_callback: StageCallback | None = None,
    ) -> None:
        self._retriever = retriever
        self._model = model
        self._settings = settings
        self._checkpoint_database_url = checkpoint_database_url
        self._stage_callback = stage_callback

    async def run(self, context: ResearchExecutionContext) -> ResearchGraphOutcome:
        """根据可解释的问题特征进入单轮或受限多 Agent 研究流程。"""
        if self._is_complex_question(context.question):
            return await self._run_complex(context)
        return await self._run_single(context)

    async def _run_single(self, context: ResearchExecutionContext) -> ResearchGraphOutcome:
        """执行可 checkpoint 的单轮 RAG 图，改写预算由状态和配置共同限制。"""
        graph = StateGraph(SingleRagState)
        graph.add_node("retrieve", cast(Any, self._single_retrieve(context)))
        graph.add_node("assess", self._single_assess)
        graph.add_node("rewrite", self._single_rewrite)
        graph.add_node("answer", self._single_answer)
        graph.add_node("clarify", self._single_clarify)
        graph.add_edge(START, "retrieve")
        graph.add_edge("retrieve", "assess")
        graph.add_conditional_edges(
            "assess",
            lambda state: state["route"],
            {"answer": "answer", "rewrite": "rewrite", "clarify": "clarify"},
        )
        graph.add_edge("rewrite", "retrieve")
        graph.add_edge("answer", END)
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
                retrieval_trace={
                    **final_state.get("retrieval_trace", {}),
                    "rewrite_attempts": final_state.get("rewrite_count", 0),
                    "mode": ResearchRunMode.SINGLE_RAG.value,
                },
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
            retrieval_trace={
                **final_state.get("retrieval_trace", {}),
                "rewrite_attempts": final_state.get("rewrite_count", 0),
                "mode": ResearchRunMode.SINGLE_RAG.value,
                "outcome": "clarification",
            },
            mode=ResearchRunMode.SINGLE_RAG.value,
        )

    def _single_retrieve(
        self, context: ResearchExecutionContext
    ) -> Callable[[SingleRagState], Awaitable[dict[str, object]]]:
        """创建绑定不可变集合 scope 的图节点，模型不会接触权限过滤参数。"""

        async def retrieve(state: SingleRagState) -> dict[str, object]:
            await self._emit(ResearchRunStage.HYBRID_RETRIEVAL, "正在检索当前集合中的原文证据。", 0)
            result: RetrievalResult = await self._retriever.retrieve(
                scope=context.retrieval_scope,
                query=state["query"],
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
        await self._emit(
            ResearchRunStage.RERANKING,
            "正在筛选可用于回答的证据。",
            len(state["evidences"]),
        )
        if state["evidences"]:
            return {"route": "answer"}
        if state["rewrite_count"] < self._settings.rag_max_query_rewrites:
            return {"route": "rewrite"}
        return {"route": "clarify"}

    async def _single_rewrite(self, state: SingleRagState) -> dict[str, object]:
        """查询改写只更新下一次检索词，不能作为答案或引用进入最终结果。"""
        rewritten = await self._model.rewrite_query(state["question"])
        return {"query": rewritten, "rewrite_count": state["rewrite_count"] + 1}

    async def _single_answer(self, state: SingleRagState) -> dict[str, object]:
        """只把 RRF 入选证据传给回答模型，并拒绝模型越界返回的 UUID。"""
        evidences = tuple(_evidence_from_state(item) for item in state["evidences"])
        await self._emit(ResearchRunStage.ANSWERING, "正在依据已检索证据整理回答。", len(evidences))
        answer = await self._model.generate_answer(question=state["question"], evidences=evidences)
        if not answer.evidence_sufficient:
            return {
                "route": "clarify",
                "clarification_question": answer.clarification_question
                or "当前证据不足以回答，请补充研究对象或限定条件。",
            }
        return {
            "answer": answer.answer,
            "cited_chunk_ids": [str(item) for item in answer.cited_chunk_ids],
        }

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

    async def _run_complex(self, context: ResearchExecutionContext) -> ResearchGraphOutcome:
        """以有限规划、受限检索和证据核验处理跨论文比较等复杂问题。"""
        await self._emit(ResearchRunStage.PREPARING, "正在拆分需要分别核验的研究子问题。", 0)
        subquestions = await self._model.plan_subquestions(
            context.question, self._settings.rag_max_subquestions
        )
        # 子问题检索是唯一可调用的工具，数量由计划与 REACT 预算共同约束。
        planned_queries = subquestions[: self._settings.rag_max_react_tool_calls]
        semaphore = asyncio.Semaphore(self._settings.rag_max_parallel_subquestions)

        async def retrieve_one(question: str) -> RetrievalResult:
            async with semaphore:
                return await self._retriever.retrieve(scope=context.retrieval_scope, query=question)

        await self._emit(ResearchRunStage.HYBRID_RETRIEVAL, "正在并行检索各子问题的原文证据。", 0)
        results = await asyncio.gather(*(retrieve_one(question) for question in planned_queries))
        evidence_by_id: dict[UUID, RetrievedEvidence] = {}
        for result in results:
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
                trace={
                    "mode": ResearchRunMode.MULTI_AGENT.value,
                    "subquestions": list(planned_queries),
                    "tool_calls": len(planned_queries),
                    "outcome": "no_knowledge",
                },
            )
        await self._emit(
            ResearchRunStage.EVIDENCE_VERIFYING,
            "正在核验原文是否支持跨论文结论。",
            len(all_evidences),
        )
        verification = await self._model.verify_evidence(
            question=context.question, evidences=all_evidences
        )
        verified_ids = set(verification.supported_chunk_ids)
        verified = tuple(item for item in all_evidences if item.chunk_id in verified_ids)
        if not verified:
            return self._clarification_outcome(
                question=context.question,
                evidences=all_evidences,
                trace={
                    "mode": ResearchRunMode.MULTI_AGENT.value,
                    "subquestions": list(planned_queries),
                    "tool_calls": len(planned_queries),
                    "unresolved_aspects": verification.unresolved_aspects,
                    "outcome": "evidence_not_supported",
                },
            )
        await self._emit(ResearchRunStage.ANSWERING, "正在综合已核验的文献证据。", len(verified))
        answer = await self._model.generate_answer(question=context.question, evidences=verified)
        if not answer.evidence_sufficient:
            return self._clarification_outcome(
                question=context.question,
                evidences=verified,
                trace={
                    "mode": ResearchRunMode.MULTI_AGENT.value,
                    "subquestions": list(planned_queries),
                    "tool_calls": len(planned_queries),
                    "unresolved_aspects": verification.unresolved_aspects,
                    "outcome": "answer_evidence_insufficient",
                },
                clarification=answer.clarification_question,
            )
        return ResearchGraphOutcome(
            status=ResearchRunStatus.COMPLETED,
            stage=ResearchRunStage.COMPLETED,
            answer=answer.answer,
            evidences=verified,
            cited_chunk_ids=tuple(answer.cited_chunk_ids),
            retrieval_trace={
                "mode": ResearchRunMode.MULTI_AGENT.value,
                "subquestions": list(planned_queries),
                "tool_calls": len(planned_queries),
                "verified_evidence_count": len(verified),
                "unresolved_aspects": verification.unresolved_aspects,
            },
            mode=ResearchRunMode.MULTI_AGENT.value,
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
        if self._stage_callback is not None:
            await self._stage_callback(stage, message, evidence_count)

    @classmethod
    def _is_complex_question(cls, question: str) -> bool:
        """首版以显式问题特征启动复杂图，避免简单问答承担多 Agent 延迟。"""
        normalized = question.lower()
        return any(marker in normalized for marker in cls._COMPLEX_MARKERS)

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
        rank=int(payload["rank"]) if payload.get("rank") is not None else None,
        source_chunk_ids=tuple(UUID(str(item)) for item in payload.get("source_chunk_ids", [])),
        parent_merged=bool(payload.get("parent_merged", False)),
    )
