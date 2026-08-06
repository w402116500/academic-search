"""研究 Agent 的结构化模型契约、调用预算和稳定异常。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.modules.rag.retrieval import RetrievedEvidence

EvidenceRef = Annotated[str, StringConstraints(pattern=r"^E[1-9][0-9]*$")]
ClaimId = Annotated[str, StringConstraints(pattern=r"^C[1-9][0-9]*$")]


class ResearchModelError(RuntimeError):
    """研究图调用聊天模型或解析其结构化输出失败时抛出。"""


class ResearchModelProtocolError(ResearchModelError):
    """模型响应违反结构化契约或 EvidenceRef 边界。"""

    def __init__(self, message: str, *, output_summary: str = "structured_output_rejected") -> None:
        super().__init__(message)
        self.diagnostics: dict[str, Any] = {"model_output_summary": output_summary}

    def add_evidence_snapshot(self, snapshot: list[dict[str, object]]) -> None:
        """Attach server-only evidence identity diagnostics before the worker persists failure."""
        self.diagnostics["evidence_snapshot"] = snapshot


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
        aliases = [
            key for key in ("router", "choice", "agent", "route", "selection") if key in value
        ]
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


class AnswerClaimDraft(BaseModel):
    """回答草稿中的一个事实性主张及其模型侧证据引用。"""

    claim_id: ClaimId
    text: str = Field(min_length=1, max_length=1_500)
    refs: list[EvidenceRef] = Field(default_factory=list, max_length=12)


class AnswerDraft(BaseModel):
    """模型根据已给证据生成的回答草稿。"""

    answer: str = Field(min_length=1, max_length=12_000)
    cited_refs: list[EvidenceRef] = Field(default_factory=list, max_length=12)
    claims: list[AnswerClaimDraft] = Field(default_factory=list, max_length=24)
    evidence_sufficient: bool
    clarification_question: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="before")
    @classmethod
    def normalize_claim_drafts(cls, data: object) -> object:
        """兼容真实模型把 claim text 写成 claim、或漏填 claim_id 的安全输出。"""
        if not isinstance(data, dict):
            return data
        claims = data.get("claims")
        if not isinstance(claims, list):
            return data
        normalized_claims: list[object] = []
        changed = False
        for index, claim in enumerate(claims, start=1):
            if not isinstance(claim, dict):
                normalized_claims.append(claim)
                continue
            normalized = dict(claim)
            if "text" not in normalized and isinstance(normalized.get("claim"), str):
                normalized["text"] = normalized["claim"]
                changed = True
            if "claim_id" not in normalized:
                normalized["claim_id"] = f"C{index}"
                changed = True
            normalized_claims.append(normalized)
        if not changed:
            return data
        return {**data, "claims": normalized_claims}

    @model_validator(mode="after")
    def sufficient_answer_must_cite_evidence(self) -> AnswerDraft:
        """证据不足允许空引用；声称证据充分时必须回链至少一个本次候选片段。"""
        if self.evidence_sufficient and not self.cited_refs:
            raise ValueError("证据充分的回答必须至少引用一个原文片段")
        if self.evidence_sufficient and not self.claims:
            raise ValueError("证据充分的回答必须列出可核验的原子主张")
        cited_refs = set(self.cited_refs)
        for claim in self.claims:
            if not set(claim.refs).issubset(cited_refs):
                raise ValueError("回答主张引用必须属于 cited_refs")
        return self


class SubquestionPlan(BaseModel):
    """复杂问题可执行的有限子问题计划。"""

    subquestions: list[str] = Field(min_length=2, max_length=8)


class EvidenceVerification(BaseModel):
    """证据核验器只能接受或拒绝候选片段，不能新造结论。"""

    supported_refs: list[EvidenceRef] = Field(default_factory=list, max_length=20)
    unresolved_aspects: list[str] = Field(default_factory=list, max_length=8)


class AnswerClaimVerificationItem(BaseModel):
    """回答中一个原子主张与实际引用片段之间的独立核验结果。"""

    claim_id: ClaimId
    claim: str = Field(min_length=1, max_length=1_500)
    supported: bool
    supporting_refs: list[EvidenceRef] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def supporting_chunks_match_verdict(self) -> AnswerClaimVerificationItem:
        """不允许核验器把无出处的主张标为支持，或为拒绝主张伪造来源。"""
        if self.supported and not self.supporting_refs:
            raise ValueError("受支持的回答主张必须关联至少一个引用片段")
        if not self.supported and self.supporting_refs:
            raise ValueError("不受支持的回答主张不能携带支持片段")
        return self


class AnswerClaimVerification(BaseModel):
    """独立核验器覆盖回答内全部事实性原子主张的结构化输出。"""

    claims: list[AnswerClaimVerificationItem] = Field(min_length=1, max_length=24)

    @model_validator(mode="before")
    @classmethod
    def normalize_claim_ids(cls, data: object) -> object:
        """兼容真实模型漏填 claim_id 的核验输出，按返回顺序补齐稳定编号。"""
        if isinstance(data, list):
            data = {"claims": data}
        if not isinstance(data, dict):
            return data
        claims = data.get("claims")
        if not isinstance(claims, list):
            return data
        normalized_claims: list[object] = []
        changed = False
        for index, claim in enumerate(claims, start=1):
            if not isinstance(claim, dict):
                normalized_claims.append(claim)
                continue
            normalized = dict(claim)
            if "claim_id" not in normalized:
                normalized["claim_id"] = f"C{index}"
                changed = True
            normalized_claims.append(normalized)
        if not changed:
            return data
        return {**data, "claims": normalized_claims}


class FinalAnswerDraft(BaseModel):
    """核验失败后的最终答案重写结果，只能使用已支持主张。"""

    answer: str = Field(min_length=1, max_length=12_000)
    cited_refs: list[EvidenceRef] = Field(default_factory=list, max_length=12)
    resolved_claim_ids: list[ClaimId] = Field(default_factory=list, max_length=24)
    evidence_insufficient_claims: list[ClaimId] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def final_answer_must_cite_evidence(self) -> FinalAnswerDraft:
        """修复后的最终答案仍必须携带可回链证据。"""
        if not self.cited_refs:
            raise ValueError("最终回答必须至少引用一个原文片段")
        return self


class PresentationAnswerDraft(BaseModel):
    """仅重组已核验主张表达的可选编辑结果。"""

    answer: str = Field(min_length=1, max_length=12_000)
    cited_refs: list[EvidenceRef] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def presentation_answer_must_cite_evidence(self) -> PresentationAnswerDraft:
        """展示编辑不能移除所有可追溯的证据引用。"""
        if not self.cited_refs:
            raise ValueError("展示编辑后的回答必须至少引用一个原文片段")
        return self


class ResearchChatModel(Protocol):
    """研究图依赖的结构化模型边界，测试可替换为无需网络的实现。"""

    async def rewrite_query(self, question: str) -> str: ...

    async def route_question(self, question: str) -> ResearchRouteDecision: ...

    async def generate_answer(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> AnswerDraft: ...

    async def plan_subquestions(self, question: str, max_subquestions: int) -> tuple[str, ...]: ...

    async def decide_research_action(
        self,
        *,
        question: str,
        available_queries: Sequence[str],
        observations: Sequence[dict[str, object]],
        tool_calls_remaining: int,
    ) -> ResearchToolAction: ...

    async def verify_evidence(
        self, *, question: str, evidences: Sequence[RetrievedEvidence]
    ) -> EvidenceVerification: ...

    async def verify_answer_claims(
        self,
        *,
        question: str,
        answer: str,
        evidences: Sequence[RetrievedEvidence],
        cited_refs: Sequence[str],
    ) -> AnswerClaimVerification: ...

    async def compose_final_answer(
        self,
        *,
        question: str,
        draft_answer: str,
        verification: AnswerClaimVerification,
        evidences: Sequence[RetrievedEvidence],
    ) -> FinalAnswerDraft: ...

    async def edit_answer_presentation(
        self,
        *,
        question: str,
        supported_claims: Sequence[AnswerClaimVerificationItem],
        allowed_refs: Sequence[str],
    ) -> PresentationAnswerDraft: ...
