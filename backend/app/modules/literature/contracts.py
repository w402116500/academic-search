"""论文题录、DOI 核验和引用导出的领域合同。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class CitationAuthor(BaseModel):
    """可直接转换为 CSL 的作者信息。"""

    literal: str | None = Field(default=None, min_length=1, max_length=500)
    given: str | None = Field(default=None, min_length=1, max_length=500)
    family: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def name_has_a_supported_shape(self) -> CitationAuthor:
        """作者必须是字面名称，或至少包含姓氏。"""
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
    """支持仅有年份的书目发布日期。"""

    year: int = Field(ge=1600, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)

    @model_validator(mode="after")
    def day_requires_month(self) -> CitationDate:
        """日期不能只表达日而没有月。"""
        if self.day is not None and self.month is None:
            raise ValueError("发布日期包含日时必须同时包含月")
        return self

    def to_csl_date_parts(self) -> list[int]:
        """按年份、月份、日期顺序生成 CSL-JSON 日期部分。"""
        parts = [self.year]
        if self.month is not None:
            parts.append(self.month)
        if self.day is not None:
            parts.append(self.day)
        return parts


class CitationMetadataStatus(StrEnum):
    """题录补全阶段的稳定状态。"""

    READY = "ready"
    PARTIAL = "partial"
    CONFLICT = "conflict"
    UNRESOLVED = "unresolved"


class CitationResolutionErrorCode(StrEnum):
    """DOI 内容协商失败的安全分类。"""

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
    """从 DOI Content Negotiation 返回的 CSL-JSON 书目字段。"""

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
    """一次 DOI 内容协商的结果，成功和失败互斥。"""

    doi: str = Field(min_length=1, max_length=512)
    record: DoiCslRecord | None = None
    error: CitationResolutionError | None = None

    @model_validator(mode="after")
    def result_has_exactly_one_outcome(self) -> DoiMetadataResolution:
        """防止调用方把失败响应误作权威元数据。"""
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
        """阻止不完整或冲突题录伪装成 ready。"""
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
