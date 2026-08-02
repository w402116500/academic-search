"""基于统一候选元数据的受限相关性评估。

该模块只解释“候选为什么可能帮助当前研究”，不判断 DOI、题录、全文权限或
论文质量。模型不能改写统一候选本身，服务端会核验它引用的标题/摘要原文。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from app.modules.search.contracts import (
    CandidateRelevanceAssessment,
    CandidateRelevanceError,
    CandidateRelevanceEvidence,
    CandidateRelevanceLevel,
    CandidateRelevanceState,
    UnifiedCandidate,
)
from app.modules.workflow.contracts import ProviderSearchQuery, ResearchScope
from app.modules.workflow.settings import WorkflowSettings
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError, model_validator


class StructuredRelevanceModel(Protocol):
    """便于测试替换的最小结构化模型接口。"""

    async def ainvoke(self, input: list[SystemMessage | HumanMessage]) -> object:
        """根据固定上下文返回一批候选的评估结果。"""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class CandidateRelevanceContext:
    """一次已确认检索可安全发送给模型的研究上下文。"""

    research_question: str
    direction_title: str
    direction_summary: str
    subtopics: tuple[str, ...]
    search_queries: tuple[str, ...]
    start_year: int | None
    end_year: int | None
    languages: tuple[str, ...]


def build_candidate_relevance_context(
    *,
    research_question: str,
    direction_options: Sequence[object],
    selected_direction_id: str | None,
    query_specs: Sequence[ProviderSearchQuery],
    scope: ResearchScope,
) -> CandidateRelevanceContext:
    """从已确认计划提取 Agent 唯一需要的研究上下文。"""
    direction = next(
        (
            item
            for item in direction_options
            if isinstance(item, dict) and item.get("id") == selected_direction_id
        ),
        {},
    )
    return CandidateRelevanceContext(
        research_question=research_question,
        direction_title=str(direction.get("title", "当前研究方向")),
        direction_summary=str(direction.get("summary", "")),
        subtopics=tuple(str(item) for item in direction.get("subtopics", [])),
        search_queries=tuple(spec.query for spec in query_specs),
        start_year=scope.start_year,
        end_year=scope.end_year,
        languages=tuple(language.value for language in scope.languages),
    )


class CandidateRelevanceModelEvidence(BaseModel):
    """模型传输层的证据格式，来源字段由服务端优先确定。"""

    quote: str = Field(min_length=1, max_length=500)
    source_field: str | None = None


class CandidateRelevanceItem(BaseModel):
    """模型输出中针对一个统一候选的扁平评估格式。

    DeepSeek 的 JSON mode 能稳定返回扁平对象，但不可靠地遵循嵌套的
    ``assessment`` 包装。该传输格式只存在于模型边界；服务端会立即转换为
    ``CandidateRelevanceAssessment``，不会改变 API 或 Redis 快照契约。
    """

    candidate_id: UUID
    level: CandidateRelevanceLevel
    study_focus: str = Field(min_length=1, max_length=600)
    reason: str = Field(min_length=1, max_length=800)
    helpful_aspect: str = Field(min_length=1, max_length=800)
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=4)
    recommendation: str = Field(min_length=1, max_length=500)
    evidence: tuple[CandidateRelevanceModelEvidence, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="before")
    @classmethod
    def normalize_unambiguous_json_shapes(cls, value: object) -> object:
        """兼容 JSON mode 的单项数组简写，不接受缺失或不可验证的证据。"""
        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        if isinstance(normalized.get("limitations"), str):
            normalized["limitations"] = [normalized["limitations"]]
        if isinstance(normalized.get("evidence"), dict):
            normalized["evidence"] = [normalized["evidence"]]
        return normalized

    def to_assessment(self, candidate: UnifiedCandidate) -> CandidateRelevanceAssessment:
        """将模型传输对象转换为服务端可验证的业务评估。"""

        def evidence_source(item: CandidateRelevanceModelEvidence) -> Literal["title", "abstract"]:
            """只接受模型给出的合法来源字段，其余情况由候选原文确定。"""
            if item.source_field == "title":
                return "title"
            if item.source_field == "abstract":
                return "abstract"
            return _resolve_evidence_source(candidate, item.quote)

        evidence = tuple(
            CandidateRelevanceEvidence(
                source_field=evidence_source(item),
                quote=item.quote,
            )
            for item in self.evidence
        )
        return CandidateRelevanceAssessment(
            level=self.level,
            study_focus=self.study_focus,
            reason=self.reason,
            helpful_aspect=self.helpful_aspect,
            limitations=self.limitations,
            recommendation=self.recommendation,
            evidence=evidence,
        )


class CandidateRelevanceBatch(BaseModel):
    """单次批量模型调用的完整、可校验结果。"""

    assessments: tuple[CandidateRelevanceItem, ...] = Field(min_length=1)


_SYSTEM_PROMPT = """你是学术文献候选相关性评估器。
只根据提供的研究上下文和每条候选的标题、摘要、年份、类型与载体判断相关性。
不得使用外部知识，不得声称读过全文，不得判断期刊等级或论文真实结论。

输出必须是一个 JSON 对象，形状严格为：
{"assessments":[{"candidate_id":"候选 UUID","level":"core","study_focus":"...",
"reason":"...","helpful_aspect":"...","limitations":["..."],"recommendation":"...",
"evidence":[{"quote":"候选标题或摘要中的逐字原文"}]}]}。
不要额外嵌套 assessment 字段。每个 candidate_id 恰好输出一次。字段要求：
1. level 只能为 core、related、background、not_recommended、insufficient_information。
2. study_focus、reason、helpful_aspect、limitations、recommendation 使用简洁中文；
   limitations 必须是字符串数组。
   study_focus 只概述这篇候选主要研究的对象、关系或方法，不能复述整段摘要。
3. evidence 的 quote 必须逐字摘自该候选给出的 title 或 abstract，不能改写、翻译或编造。
4. abstract 缺失时必须使用 insufficient_information，且不得推测研究对象、方法或结论。
5. 相关性不是质量分数，也不能承诺该候选可以进入 RAG。
"""


class OpenAICompatibleCandidateRelevanceEvaluator:
    """使用现有 OpenAI 兼容聊天模型评估统一候选的语义关系。"""

    def __init__(
        self,
        settings: WorkflowSettings,
        *,
        model: StructuredRelevanceModel | None = None,
    ) -> None:
        self._settings = settings
        self._model = model or self._create_model(settings)

    async def assess(
        self,
        *,
        context: CandidateRelevanceContext,
        candidates: Sequence[UnifiedCandidate],
    ) -> tuple[UnifiedCandidate, ...]:
        """批量评估已经规整的候选，并把验证后的结果附回原对象。"""
        if not candidates:
            return ()

        candidates_with_abstract = [candidate for candidate in candidates if candidate.abstract]
        without_abstract = [candidate for candidate in candidates if not candidate.abstract]
        assessed_candidates: dict[UUID, UnifiedCandidate] = {
            candidate.candidate_id: self._insufficient_candidate(candidate)
            for candidate in without_abstract
        }
        if candidates_with_abstract:
            try:
                raw_result = await self._model.ainvoke(
                    [
                        SystemMessage(_SYSTEM_PROMPT),
                        HumanMessage(
                            self._build_payload(
                                context,
                                candidates_with_abstract,
                                abstract_max_characters=(
                                    self._settings.workflow_relevance_abstract_max_characters
                                ),
                            )
                        ),
                    ]
                )
                result = CandidateRelevanceBatch.model_validate(raw_result)
            except ValidationError:
                assessed_candidates.update(
                    {
                        candidate.candidate_id: mark_candidate_relevance_failed(
                            candidate,
                            "候选相关性模型返回的数据不符合约定，无法生成可靠理由。",
                            code="candidate_relevance_output_invalid",
                        )
                        for candidate in candidates_with_abstract
                    }
                )
            except Exception:
                assessed_candidates.update(
                    {
                        candidate.candidate_id: mark_candidate_relevance_failed(
                            candidate,
                            "候选相关性模型暂时不可用，请稍后重试。",
                            code="candidate_relevance_model_unavailable",
                        )
                        for candidate in candidates_with_abstract
                    }
                )
            else:
                assessments, errors = self._validate_batch(candidates_with_abstract, result)
                for candidate in candidates_with_abstract:
                    assessment = assessments.get(candidate.candidate_id)
                    if assessment is None:
                        assessed_candidates[candidate.candidate_id] = (
                            mark_candidate_relevance_failed(
                                candidate,
                                errors[candidate.candidate_id],
                                code="candidate_relevance_output_invalid",
                            )
                        )
                        continue
                    assessed_candidates[candidate.candidate_id] = candidate.model_copy(
                        update={
                            "relevance_state": CandidateRelevanceState.COMPLETED,
                            "relevance_assessment": assessment,
                            "relevance_error": None,
                        }
                    )
        return tuple(assessed_candidates[candidate.candidate_id] for candidate in candidates)

    @staticmethod
    def _create_model(settings: WorkflowSettings) -> StructuredRelevanceModel:
        """创建 JSON 模式模型，具体证据真实性仍由本模块二次校验。"""
        chat_model = ChatOpenAI(
            model=settings.active_chat_model,
            api_key=settings.active_api_key,
            base_url=settings.active_base_url,
            temperature=0,
            timeout=settings.workflow_relevance_timeout_seconds,
            max_retries=0,
            # 当前依赖版本的 Pyright 类型定义遗漏了运行时已支持的参数；DeepSeek 也使用它
            # 限制 completion 长度。保留显式参数，避免 model_kwargs 触发运行时警告。
            max_tokens=settings.workflow_relevance_max_output_tokens,  # pyright: ignore[reportCallIssue]
        )
        return chat_model.with_structured_output(CandidateRelevanceBatch, method="json_mode")

    @staticmethod
    def _build_payload(
        context: CandidateRelevanceContext,
        candidates: Sequence[UnifiedCandidate],
        *,
        abstract_max_characters: int = 3_000,
    ) -> str:
        """只序列化相关性判断必要字段，避免把来源原始记录和下载链接送入模型。"""
        data = {
            "research_context": {
                "question": context.research_question,
                "direction": {
                    "title": context.direction_title,
                    "summary": context.direction_summary,
                    "subtopics": context.subtopics,
                },
                "scope": {
                    "start_year": context.start_year,
                    "end_year": context.end_year,
                    "languages": context.languages,
                },
                "search_queries": context.search_queries,
            },
            "candidates": [
                {
                    "candidate_id": str(candidate.candidate_id),
                    "title": candidate.title,
                    # 摘要仅用于候选相关性解释，限制长度不会影响长期题录或全文内容。
                    "abstract": candidate.abstract[:abstract_max_characters]
                    if candidate.abstract is not None
                    else None,
                    "authors": [author.name for author in candidate.authors[:8]],
                    "published_year": candidate.published_year,
                    "venue": candidate.venue,
                    "document_type": candidate.document_type,
                    "language": candidate.language.value,
                    "triage_warnings": [warning.value for warning in candidate.triage.warnings]
                    if candidate.triage
                    else [],
                }
                for candidate in candidates
            ],
        }
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _validate_batch(
        candidates: Sequence[UnifiedCandidate],
        result: CandidateRelevanceBatch,
    ) -> tuple[dict[UUID, CandidateRelevanceAssessment], dict[UUID, str]]:
        """逐条核验模型输出，坏记录不能抹掉同批已通过验证的结果。"""
        expected = {candidate.candidate_id: candidate for candidate in candidates}
        grouped: dict[UUID, list[CandidateRelevanceItem]] = {}
        for item in result.assessments:
            if item.candidate_id in expected:
                grouped.setdefault(item.candidate_id, []).append(item)

        valid: dict[UUID, CandidateRelevanceAssessment] = {}
        errors: dict[UUID, str] = {}
        for candidate_id, candidate in expected.items():
            items = grouped.get(candidate_id, [])
            if not items:
                errors[candidate_id] = "模型没有返回这条候选的相关性评估结果。"
                continue
            if len(items) != 1:
                errors[candidate_id] = "模型为同一候选返回了重复的相关性评估结果。"
                continue
            assessment = items[0].to_assessment(candidate)
            evidence_error = OpenAICompatibleCandidateRelevanceEvaluator._validate_evidence(
                candidate,
                assessment,
            )
            if evidence_error is not None:
                errors[candidate_id] = evidence_error
                continue
            valid[candidate_id] = assessment
        return valid, errors

    @staticmethod
    def _validate_evidence(
        candidate: UnifiedCandidate,
        assessment: CandidateRelevanceAssessment,
    ) -> str | None:
        """验证每条判断依据都能在同一候选的标题或摘要中找到。"""
        for evidence in assessment.evidence:
            source = candidate.title if evidence.source_field == "title" else candidate.abstract
            if source is None or _normalize(evidence.quote) not in _normalize(source):
                return "模型理由包含无法在候选标题或摘要中核对的依据。"
        return None

    @staticmethod
    def _insufficient_candidate(candidate: UnifiedCandidate) -> UnifiedCandidate:
        """无摘要时不调用模型，明确标注可判断信息不足的事实状态。"""
        assessment = CandidateRelevanceAssessment(
            level=CandidateRelevanceLevel.INSUFFICIENT_INFORMATION,
            study_focus=(
                f"目前只能从题目确认它涉及“{candidate.title}”，缺少摘要，无法可靠概括研究内容。"
            ),
            reason="当前候选没有摘要，无法可靠判断它是否覆盖本研究的对象、关系或研究设计。",
            helpful_aspect="可先作为待核对题录保留，补充摘要或查看原文后再判断。",
            limitations=("缺少摘要，尚不能确认研究对象、方法和结论。",),
            recommendation="建议先补充摘要或打开原文，再决定是否优先获取全文。",
            evidence=(CandidateRelevanceEvidence(source_field="title", quote=candidate.title),),
        )
        return candidate.model_copy(
            update={
                "relevance_state": CandidateRelevanceState.COMPLETED,
                "relevance_assessment": assessment,
                "relevance_error": None,
            }
        )


def mark_candidate_relevance_failed(
    candidate: UnifiedCandidate,
    message: str,
    *,
    code: str,
) -> UnifiedCandidate:
    """把模型或配置失败保存在单个候选上，绝不降级回关键词推测。"""
    return candidate.model_copy(
        update={
            "relevance_state": CandidateRelevanceState.FAILED,
            "relevance_assessment": None,
            "relevance_error": CandidateRelevanceError(
                code=code,
                message=message,
                retryable=True,
            ),
        }
    )


def skip_candidate_relevance(candidate: UnifiedCandidate) -> UnifiedCandidate:
    """基础初筛已排除的候选不消耗模型调用，也不能永久停在“分析中”。"""
    return candidate.model_copy(
        update={
            "relevance_state": CandidateRelevanceState.SKIPPED,
            "relevance_assessment": None,
            "relevance_error": None,
        }
    )


def _normalize(value: str) -> str:
    """松弛处理空白和大小写后比较证据，仍不允许改变原文词语。"""
    return " ".join(value.casefold().split())


def _resolve_evidence_source(
    candidate: UnifiedCandidate,
    quote: str,
) -> Literal["title", "abstract"]:
    """从候选自身元数据确定证据字段；无法定位时选标题并由验证器拒绝。"""
    normalized_quote = _normalize(quote)
    if normalized_quote in _normalize(candidate.title):
        return "title"
    if candidate.abstract is not None and normalized_quote in _normalize(candidate.abstract):
        return "abstract"
    return "title"
