"""研究 Agent 的 OpenAI 兼容结构化模型适配器。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.core.workflow_settings import WorkflowSettings
from app.modules.agents.contracts import (
    AnswerClaimVerification,
    AnswerDraft,
    EvidenceVerification,
    QueryRewrite,
    ResearchModelError,
    ResearchRouteDecision,
    ResearchToolAction,
    SubquestionPlan,
)
from app.modules.agents.prompts import (
    REWRITE_QUERY_SYSTEM,
    ROUTE_QUESTION_SYSTEM,
    answer_claim_verification_system,
    answer_system,
    evidence_verification_system,
    research_action_system,
    subquestion_system,
)
from app.modules.rag.retrieval import RetrievedEvidence
from app.modules.research.settings import ResearchSettings


class OpenAICompatibleResearchModel:
    """通过 LangChain 调用 DeepSeek 或其他 OpenAI 兼容聊天模型。"""

    def __init__(self, settings: WorkflowSettings, research_settings: ResearchSettings) -> None:
        self._client = ChatOpenAI(
            model=settings.active_chat_model,
            api_key=settings.active_api_key,
            base_url=settings.active_base_url,
            temperature=0,
            timeout=research_settings.rag_chat_timeout_seconds,
            max_retries=0,
        )

    async def rewrite_query(self, question: str) -> str:
        result = await self._invoke_structured(
            QueryRewrite,
            system=REWRITE_QUERY_SYSTEM,
            human=question,
        )
        return result.query.strip()

    async def route_question(self, question: str) -> ResearchRouteDecision:
        return await self._invoke_structured(
            ResearchRouteDecision,
            system=ROUTE_QUESTION_SYSTEM,
            human=question,
        )

    async def generate_answer(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> AnswerDraft:
        result = await self._invoke_structured(
            AnswerDraft,
            system=answer_system(evidences),
            human=question,
        )
        allowed_ids = {evidence.chunk_id for evidence in evidences}
        if not set(result.cited_chunk_ids).issubset(allowed_ids):
            raise ResearchModelError("回答模型返回了不属于当前研究集合的引用标识。")
        return result

    async def plan_subquestions(self, question: str, max_subquestions: int) -> tuple[str, ...]:
        result = await self._invoke_structured(
            SubquestionPlan,
            system=subquestion_system(max_subquestions),
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
        result = await self._invoke_structured(
            ResearchToolAction,
            system=research_action_system(
                available_queries,
                observations,
                tool_calls_remaining,
            ),
            human=question,
        )
        if result.action == "retrieve" and result.query not in available_queries:
            raise ResearchModelError("复杂研究控制器选择了规划范围外的检索查询。")
        return result

    async def verify_evidence(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> EvidenceVerification:
        result = await self._invoke_structured(
            EvidenceVerification,
            system=evidence_verification_system(evidences),
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
        result = await self._invoke_structured(
            AnswerClaimVerification,
            system=answer_claim_verification_system(evidences),
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
        try:
            model = self._client.with_structured_output(schema, method="json_mode")
            json_system = f"{system}\n\n只返回符合字段约束的有效 JSON 对象，不要输出其他内容。"
            return await model.ainvoke([SystemMessage(json_system), HumanMessage(human)])
        except (ValidationError, ValueError) as exc:
            raise ResearchModelError("研究模型返回了不符合结构约束的结果。") from exc
        except Exception as exc:
            raise ResearchModelError("研究聊天模型暂时不可用。") from exc
