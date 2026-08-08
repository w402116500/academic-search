"""文献来源适配器之间共享的内部数据契约。

这些模型代表短生命周期的搜索候选，绝不等同于 PostgreSQL 中已核验的
``papers`` 表记录。正式题录核验与入库会在用户明确选择文献后单独执行。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from app.modules.literature import contracts as literature_contracts


class SourceName(StrEnum):
    """当前规划中的外部文献来源名称。"""

    OPENALEX = "openalex"
    CROSSREF = "crossref"
    ARXIV = "arxiv"
    SEMANTIC_SCHOLAR = "semantic_scholar"


class CandidateLanguage(StrEnum):
    """候选文献的主语言分类，用于展示与前端筛选。

    ``OTHER`` 与 ``UNKNOWN`` 必须保留：公开来源并不总是只返回中英文记录，
    也不应把无法可靠判断的文本错误标成英文。
    """

    CHINESE = "zh"
    ENGLISH = "en"
    OTHER = "other"
    UNKNOWN = "unknown"


class ProviderErrorCode(StrEnum):
    """对前端和编排器稳定暴露的来源失败类别。"""

    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    REMOTE_ERROR = "remote_error"
    INVALID_RESPONSE = "invalid_response"


class CandidateAuthor(BaseModel):
    """候选文献中的有序作者信息，不建立长期作者实体。"""

    name: str = Field(min_length=1, max_length=500)
    source_author_id: str | None = None


class ProviderQuery(BaseModel):
    """搜索编排器传给单个 Provider 的受限查询输入。"""

    # Provider 只能接收已确认方向生成的查询词，避免直接把任意 URL 交给网络层。
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=25, ge=1, le=100)
    from_publication_year: int | None = Field(default=None, ge=1600, le=2100)
    to_publication_year: int | None = Field(default=None, ge=1600, le=2100)

    @model_validator(mode="after")
    def publication_year_range_is_valid(self) -> ProviderQuery:
        """确保年份范围从早到晚，避免向来源发送相互矛盾的过滤条件。"""
        if (
            self.from_publication_year is not None
            and self.to_publication_year is not None
            and self.from_publication_year > self.to_publication_year
        ):
            raise ValueError("起始发表年份不能晚于结束发表年份")

        return self


class RawCandidate(BaseModel):
    """单个来源返回并完成字段映射的临时候选文献。"""

    source: SourceName
    source_record_id: str = Field(min_length=1, max_length=512)
    source_record_url: str | None = None
    title: str = Field(min_length=1, max_length=5000)
    # 来源直接声明的语言优先于后续文本识别；缺失时保留 None 而非伪造结果。
    language: CandidateLanguage | None = None
    authors: tuple[CandidateAuthor, ...] = ()
    abstract: str | None = None
    published_year: int | None = Field(default=None, ge=1600, le=2100)
    published_date: literature_contracts.CitationDate | None = None
    doi: str | None = None
    venue: str | None = None
    document_type: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    article_number: str | None = None
    publisher: str | None = None
    citation_count: int | None = Field(default=None, ge=0)
    landing_url: str | None = None
    open_access_url: str | None = None
    fulltext_url: str | None = None
    is_open_access: bool | None = None


class ProviderError(BaseModel):
    """单个来源失败的安全摘要，禁止保存响应正文或密钥。"""

    code: ProviderErrorCode
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool
    http_status_code: int | None = Field(default=None, ge=100, le=599)


class ProviderSearchResult(BaseModel):
    """Provider 的完整一次执行结果，失败不会中断其他来源。"""

    provider: SourceName
    candidates: tuple[RawCandidate, ...] = ()
    retrieved_at: datetime
    error: ProviderError | None = None

    @model_validator(mode="after")
    def failed_result_cannot_contain_candidates(self) -> ProviderSearchResult:
        """保证失败语义明确，避免编排器误用部分未知状态的结果。"""
        if self.error is not None and self.candidates:
            raise ValueError("失败的来源结果不能同时包含候选文献")

        return self


class TriageReasonCode(StrEnum):
    """候选初筛的稳定原因码，供前端文案和后续分析统一使用。"""

    MISSING_TITLE = "missing_title"
    UNSUPPORTED_DOCUMENT_TYPE = "unsupported_document_type"
    YEAR_OUT_OF_RANGE = "year_out_of_range"
    PREPRINT_ONLY = "preprint_only"
    MISSING_ABSTRACT = "missing_abstract"
    MISSING_DOI = "missing_doi"
    METADATA_CONFLICT = "metadata_conflict"


class TriageDecision(BaseModel):
    """规则初筛的可解释结果，不包含模型生成的相关性判断。"""

    included: bool
    exclusion_reasons: tuple[TriageReasonCode, ...] = ()
    warnings: tuple[TriageReasonCode, ...] = ()


class CandidateRelevanceLevel(StrEnum):
    """统一候选与当前已确认研究方向的关系层级。"""

    CORE = "core"  # 可优先审核的核心研究候选。
    RELATED = "related"  # 与研究问题存在直接关联，但不一定覆盖全部关系。
    BACKGROUND = "background"  # 可补充概念或背景，不能单独承担核心证据。
    NOT_RECOMMENDED = "not_recommended"  # 现有标题和摘要不支持优先保留。
    INSUFFICIENT_INFORMATION = "insufficient_information"  # 公开元数据不足，无法可靠判断。


class CandidateRelevanceState(StrEnum):
    """短期候选相关性评估的处理状态。"""

    PENDING = "pending"  # 候选已展示，等待模型批量评估。
    COMPLETED = "completed"  # 已得到并通过服务端证据校验的评估。
    EXCLUDED = "excluded"  # 已完成处理但不进入用户筛选，仅保留短期审计。
    FAILED = "failed"  # 仅兼容旧快照；新链路技术异常会自动恢复或安全排除。
    SKIPPED = "skipped"  # 未通过基础初筛，因此没有进入语义评估。


class CandidateRelevanceEvidence(BaseModel):
    """支撑系统相关性判断的一段候选标题或摘要原文。"""

    source_field: Literal["title", "abstract"]
    quote: str = Field(min_length=1, max_length=500)


class CandidateRelevanceAssessment(BaseModel):
    """候选相关性 Agent 的可展示结构化输出，不表示论文质量评分。"""

    level: CandidateRelevanceLevel
    # 面向用户的简短概述。它与原始摘要不同，专门回答“这篇主要研究什么”。
    study_focus: str = Field(min_length=1, max_length=600)
    reason: str = Field(min_length=1, max_length=800)
    helpful_aspect: str = Field(min_length=1, max_length=800)
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=4)
    recommendation: str = Field(min_length=1, max_length=500)
    evidence: tuple[CandidateRelevanceEvidence, ...] = Field(min_length=1, max_length=3)


class CandidateRelevanceError(BaseModel):
    """相关性评估失败时给前端展示的稳定摘要。"""

    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool


class CandidateLinks(BaseModel):
    """合并候选对外展示的链接集合，链接的来源另由 field_provenance 说明。"""

    landing_url: str | None = None
    open_access_url: str | None = None
    fulltext_url: str | None = None


class CandidatePdfAvailabilityStatus(StrEnum):
    """候选公开 PDF 可得性的稳定用户状态。"""

    AVAILABLE = "available"
    REQUIRES_UPLOAD = "requires_upload"


class CandidatePdfAvailability(BaseModel):
    """筛选页只暴露可行动状态，不携带内部探测失败原因。"""

    status: CandidatePdfAvailabilityStatus


class UnifiedCandidate(BaseModel):
    """多个来源合并后的临时候选文献。

    它只存在于搜索处理流程、Redis 缓存或前端状态中。用户明确选择并通过正式
    题录核验前，绝不能将它当作数据库中已确认的 ``papers`` 记录。
    """

    # 临时 ID 让前端选择候选时无需依赖来源的外部 ID，也不与持久化论文 ID 混用。
    candidate_id: UUID = Field(default_factory=uuid4)
    doi: str | None = None
    title: str = Field(min_length=1, max_length=5000)
    title_key: str = Field(min_length=1, max_length=5000)
    # 统一候选始终提供可展示的语言状态，兼容历史 Redis 快照中缺少该字段的记录。
    language: CandidateLanguage = CandidateLanguage.UNKNOWN
    authors: tuple[CandidateAuthor, ...] = ()
    abstract: str | None = None
    published_year: int | None = Field(default=None, ge=1600, le=2100)
    published_date: literature_contracts.CitationDate | None = None
    venue: str | None = None
    document_type: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    article_number: str | None = None
    publisher: str | None = None
    citation_counts_by_source: dict[str, int] = Field(default_factory=dict)
    links: CandidateLinks = Field(default_factory=CandidateLinks)
    is_open_access: bool | None = None
    source_records: tuple[RawCandidate, ...] = Field(min_length=1)
    field_provenance: dict[str, SourceName] = Field(default_factory=dict)
    conflicts: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    triage: TriageDecision | None = None
    # 评估只存在于 Redis 搜索会话，不能替代论文准入或写入长期 papers 表。
    relevance_state: CandidateRelevanceState = CandidateRelevanceState.PENDING
    relevance_assessment: CandidateRelevanceAssessment | None = None
    relevance_error: CandidateRelevanceError | None = None
    # 题录由用户复制或加入研究集合时按需补全，不能在候选召回阶段伪造为已核验。
    citation: literature_contracts.CitationMetadata | None = None
    pdf_availability: CandidatePdfAvailability | None = None


class CandidateProcessingResult(BaseModel):
    """一次多来源候选处理的聚合结果与可观测统计。"""

    candidates: tuple[UnifiedCandidate, ...]
    provider_errors: dict[SourceName, ProviderError] = Field(default_factory=dict)
    raw_candidate_count: int = Field(ge=0)
    deduplicated_candidate_count: int = Field(ge=0)
    included_candidate_count: int = Field(ge=0)
