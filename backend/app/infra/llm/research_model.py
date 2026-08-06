"""研究 Agent 的 OpenAI 兼容结构化模型适配器。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.core.workflow_settings import WorkflowSettings
from app.modules.agents.contracts import (
    AnswerClaimVerification,
    AnswerClaimVerificationItem,
    AnswerDraft,
    EvidenceVerification,
    FinalAnswerDraft,
    PresentationAnswerDraft,
    QueryRewrite,
    ResearchModelError,
    ResearchModelProtocolError,
    ResearchRouteDecision,
    ResearchToolAction,
    SubquestionPlan,
)
from app.modules.agents.evidence_refs import (
    canonical_answer_cited_refs,
    evidence_refs_for,
    invalid_evidence_refs,
    recover_answer_prose_citations,
)
from app.modules.agents.prompts import (
    REWRITE_QUERY_SYSTEM,
    ROUTE_QUESTION_SYSTEM,
    answer_claim_verification_system,
    answer_system,
    evidence_verification_system,
    final_answer_composer_system,
    presentation_editor_system,
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
        allowed_refs = set(evidence_refs_for(evidences))
        self._validate_refs(result.cited_refs, allowed_refs, "回答模型")
        cited_ref_set = set(result.cited_refs)
        for claim in result.claims:
            self._validate_refs(claim.refs, allowed_refs, "回答模型主张")
            if not set(claim.refs).issubset(cited_ref_set):
                raise ResearchModelProtocolError("回答模型主张引用不属于 cited_refs。")
        if result.evidence_sufficient:
            # 仅在正文完全没有任何引用外观时，才允许依据逐句完整匹配的 claims 补标。
            # 已存在的 E 编号或用户编号必须保留给严格校验拒绝，不能与恢复结果混用。
            recovered_answer = recover_answer_prose_citations(
                result.answer,
                result.claims,
                result.cited_refs,
            )
            if recovered_answer is not None:
                result = result.model_copy(update={"answer": recovered_answer})
            cited_refs = self._canonical_answer_refs(
                result.answer, evidences, result.cited_refs, "回答模型"
            )
            result = result.model_copy(update={"cited_refs": list(cited_refs)})
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
        self._validate_refs(result.supported_refs, set(evidence_refs_for(evidences)), "证据核验器")
        return result

    async def verify_answer_claims(
        self,
        *,
        question: str,
        answer: str,
        evidences: Sequence[RetrievedEvidence],
        cited_refs: Sequence[str],
    ) -> AnswerClaimVerification:
        snapshot_refs = set(evidence_refs_for(evidences))
        cited_ref_set = set(cited_refs)
        self._validate_refs(cited_refs, snapshot_refs, "回答主张核验输入")
        result = await self._invoke_structured(
            AnswerClaimVerification,
            system=answer_claim_verification_system(evidences, cited_refs=cited_refs),
            human=f"研究问题：{question}\n\n待核验回答：\n{answer}",
        )
        for item in result.claims:
            if item.claim not in answer:
                raise ResearchModelProtocolError("回答主张核验器返回了不属于回答正文的主张。")
            self._validate_refs(item.supporting_refs, cited_ref_set, "回答主张核验器")
        return result

    async def edit_answer_presentation(
        self,
        *,
        question: str,
        supported_claims: Sequence[AnswerClaimVerificationItem],
        allowed_refs: Sequence[str],
    ) -> PresentationAnswerDraft:
        """Re-express only independently supported claims without receiving evidence text."""
        if not supported_claims or any(not item.supported for item in supported_claims):
            raise ResearchModelProtocolError("展示编辑器只能接收已支持的回答主张。")
        allowed_ref_set = set(allowed_refs)
        for item in supported_claims:
            self._validate_refs(item.supporting_refs, allowed_ref_set, "展示编辑器输入")
        supported_claims_payload = [
            {
                "claim_id": item.claim_id,
                "text": item.claim,
                "refs": list(item.supporting_refs),
            }
            for item in supported_claims
        ]
        result = await self._invoke_structured(
            PresentationAnswerDraft,
            system=presentation_editor_system(),
            human=(
                f"研究问题：{question}\n\n"
                "已支持主张 JSON：\n"
                f"{json.dumps(supported_claims_payload, ensure_ascii=False)}"
            ),
        )
        self._validate_refs(result.cited_refs, allowed_ref_set, "展示编辑器")
        return result

    async def compose_final_answer(
        self,
        *,
        question: str,
        draft_answer: str,
        verification: AnswerClaimVerification,
        evidences: Sequence[RetrievedEvidence],
    ) -> FinalAnswerDraft:
        result = await self._invoke_structured(
            FinalAnswerDraft,
            system=final_answer_composer_system(evidences),
            human=(
                f"研究问题：{question}\n\n"
                f"回答草稿：\n{draft_answer}\n\n"
                f"核验结果 JSON：\n{verification.model_dump_json()}"
            ),
        )
        self._validate_refs(result.cited_refs, set(evidence_refs_for(evidences)), "最终答案编辑器")
        cited_refs = self._canonical_answer_refs(
            result.answer, evidences, result.cited_refs, "最终答案编辑器"
        )
        known_claim_ids = {item.claim_id for item in verification.claims}
        returned_claim_ids = set(result.resolved_claim_ids) | set(
            result.evidence_insufficient_claims
        )
        if not returned_claim_ids.issubset(known_claim_ids):
            raise ResearchModelProtocolError("最终答案编辑器返回了不属于核验结果的 claim_id。")
        return result.model_copy(update={"cited_refs": list(cited_refs)})

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
            raise ResearchModelProtocolError("研究模型返回了不符合结构约束的结果。") from exc
        except Exception as exc:
            raise ResearchModelError("研究聊天模型暂时不可用。") from exc

    @staticmethod
    def _validate_refs(refs: Sequence[str], allowed_refs: set[str], actor: str) -> None:
        invalid = invalid_evidence_refs(refs, allowed_refs)
        if invalid:
            raise ResearchModelProtocolError(f"{actor}返回了不属于当前证据快照的引用标识。")

    @staticmethod
    def _canonical_answer_refs(
        answer: str,
        evidences: Sequence[RetrievedEvidence],
        cited_refs: Sequence[str],
        actor: str,
    ) -> tuple[str, ...]:
        try:
            return canonical_answer_cited_refs(answer, evidences, cited_refs)
        except ValueError as exc:
            raise ResearchModelProtocolError(f"{actor}正文引用无效，无法映射到证据快照。") from exc
