"""文献来源适配器之间共享的内部数据契约。

这些模型代表短生命周期的搜索候选，绝不等同于 PostgreSQL 中已核验的
``papers`` 表记录。正式题录核验与入库会在用户明确选择文献后单独执行。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class SourceName(StrEnum):
    """当前规划中的外部文献来源名称。"""

    OPENALEX = "openalex"
    CROSSREF = "crossref"
    ARXIV = "arxiv"
    SEMANTIC_SCHOLAR = "semantic_scholar"


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


class CitationAuthor(BaseModel):
    """可直接转换为 CSL 的作者信息。

    文献来源的候选作者只有展示姓名，而 DOI Content Negotiation 通常会给出
    ``given`` 与 ``family``。保留两种形态可避免为了生成 APA 等格式而猜测姓名
    的姓与名边界，尤其是中文姓名和机构作者。
    """

    literal: str | None = Field(default=None, min_length=1, max_length=500)
    given: str | None = Field(default=None, min_length=1, max_length=500)
    family: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def name_has_a_supported_shape(self) -> CitationAuthor:
        """作者必须是字面名称，或至少包含姓氏，避免输出无效 CSL 姓名对象。"""
        if self.literal is not None:
            return self

        if self.family is None:
            raise ValueError("作者必须提供 literal 或 family 字段")

        return self

    def display_name(self) -> str:
        """返回用于冲突检测、调试和 BibTeX 输入的稳定显示姓名。"""
        if self.literal is not None:
            return self.literal

        return " ".join(part for part in (self.given, self.family) if part is not None)

    def to_csl_json(self) -> dict[str, str]:
        """转换为 citeproc-py 所需的 CSL-JSON 作者对象。"""
        if self.literal is not None:
            return {"literal": self.literal}

        assert self.family is not None
        result: dict[str, str] = {"family": self.family}

        if self.given is not None:
            result["given"] = self.given

        return result


class CitationDate(BaseModel):
    """支持仅有年份的书目发布日期，并可无损转为 CSL ``date-parts``。"""

    year: int = Field(ge=1600, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)

    @model_validator(mode="after")
    def day_requires_month(self) -> CitationDate:
        """CSL 日期不能只表达日而没有月，因此在边界处拒绝该无效组合。"""
        if self.day is not None and self.month is None:
            raise ValueError("发布日期包含日时必须同时包含月")

        return self

    def to_csl_date_parts(self) -> list[int]:
        """按年份、月份、日期顺序生成 CSL-JSON 的单个日期部分。"""
        parts = [self.year]

        if self.month is not None:
            parts.append(self.month)

        if self.day is not None:
            parts.append(self.day)

        return parts


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
    authors: tuple[CandidateAuthor, ...] = ()
    abstract: str | None = None
    published_year: int | None = Field(default=None, ge=1600, le=2100)
    published_date: CitationDate | None = None
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


class CandidateLinks(BaseModel):
    """合并候选对外展示的链接集合，链接的来源另由 field_provenance 说明。"""

    landing_url: str | None = None
    open_access_url: str | None = None
    fulltext_url: str | None = None


class CitationMetadataStatus(StrEnum):
    """题录补全阶段的稳定状态，供前端决定能否开放复制引用。"""

    READY = "ready"
    PARTIAL = "partial"
    CONFLICT = "conflict"
    UNRESOLVED = "unresolved"


class CitationResolutionErrorCode(StrEnum):
    """DOI 内容协商失败的安全分类，不暴露上游响应正文。"""

    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    REMOTE_ERROR = "remote_error"
    INVALID_RESPONSE = "invalid_response"


class CitationResolutionError(BaseModel):
    """DOI 内容协商失败的可展示摘要。"""

    code: CitationResolutionErrorCode
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool
    http_status_code: int | None = Field(default=None, ge=100, le=599)


class DoiCslRecord(BaseModel):
    """从 DOI Content Negotiation 返回的 CSL-JSON 提取出的权威书目字段。"""

    source_url: str = Field(min_length=1, max_length=5000)
    doi: str | None = None
    authors: tuple[CitationAuthor, ...] = ()
    title: str | None = None
    document_type: str | None = None
    issued_date: CitationDate | None = None
    venue: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    article_number: str | None = None
    publisher: str | None = None
    url: str | None = None


class DoiMetadataResolution(BaseModel):
    """一次 DOI 内容协商的结果，成功记录与失败信息互斥。"""

    doi: str = Field(min_length=1, max_length=512)
    record: DoiCslRecord | None = None
    error: CitationResolutionError | None = None

    @model_validator(mode="after")
    def result_has_exactly_one_outcome(self) -> DoiMetadataResolution:
        """防止调用方把失败响应误作可合并的权威元数据。"""
        if (self.record is None) == (self.error is None):
            raise ValueError("DOI 解析结果必须且只能包含 record 或 error")

        return self


class CitationMetadata(BaseModel):
    """可追溯的格式中立题录，是所有导出格式的唯一输入。"""

    status: CitationMetadataStatus
    authors: tuple[CitationAuthor, ...] = ()
    title: str = Field(min_length=1, max_length=5000)
    document_type: str | None = None
    issued_date: CitationDate | None = None
    venue: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    article_number: str | None = None
    publisher: str | None = None
    doi: str | None = None
    url: str | None = None
    missing_fields: tuple[str, ...] = ()
    conflicts: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    field_provenance: dict[str, str] = Field(default_factory=dict)
    resolution_error: CitationResolutionError | None = None

    @model_validator(mode="after")
    def status_matches_metadata_outcome(self) -> CitationMetadata:
        """阻止调用方把缺失、冲突或失败题录伪装成可直接复制的 ready 状态。"""
        if self.status is CitationMetadataStatus.READY:
            required_values = (
                self.authors,
                self.document_type,
                self.issued_date,
                self.doi,
                self.url,
            )

            if (
                any(value is None or value == () for value in required_values)
                or self.missing_fields
                or self.conflicts
                or self.resolution_error is not None
            ):
                raise ValueError("ready 题录必须完整、无冲突且没有 DOI 解析错误")

        if self.status is CitationMetadataStatus.UNRESOLVED and self.resolution_error is None:
            raise ValueError("unresolved 题录必须携带 DOI 解析错误")

        return self


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
    authors: tuple[CandidateAuthor, ...] = ()
    abstract: str | None = None
    published_year: int | None = Field(default=None, ge=1600, le=2100)
    published_date: CitationDate | None = None
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
    # 题录由用户复制或加入研究集合时按需补全，不能在候选召回阶段伪造为已核验。
    citation: CitationMetadata | None = None


class CandidateProcessingResult(BaseModel):
    """一次多来源候选处理的聚合结果与可观测统计。"""

    candidates: tuple[UnifiedCandidate, ...]
    provider_errors: dict[SourceName, ProviderError] = Field(default_factory=dict)
    raw_candidate_count: int = Field(ge=0)
    deduplicated_candidate_count: int = Field(ge=0)
    included_candidate_count: int = Field(ge=0)
