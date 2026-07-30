"""全文获取阶段的稳定输入输出契约。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class FulltextAcquisitionStatus(StrEnum):
    """全文获取任务对调用方暴露的最终状态。"""

    AVAILABLE = "available"
    REQUIRES_UPLOAD = "requires_upload"
    REJECTED = "rejected"
    FAILED = "failed"


class FulltextAcquisitionErrorCode(StrEnum):
    """全文获取失败的可展示、可统计原因码。"""

    CITATION_NOT_READY = "citation_not_ready"
    MISSING_DOI = "missing_doi"
    DOI_MISMATCH = "doi_mismatch"
    NOT_OPEN_ACCESS = "not_open_access"
    MISSING_FULLTEXT_URL = "missing_fulltext_url"
    INVALID_URL = "invalid_url"
    UNSAFE_URL = "unsafe_url"
    REDIRECT_LIMIT_EXCEEDED = "redirect_limit_exceeded"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    REMOTE_ERROR = "remote_error"
    FILE_TOO_LARGE = "file_too_large"
    INVALID_CONTENT_TYPE = "invalid_content_type"
    INVALID_PDF = "invalid_pdf"
    STORAGE_ERROR = "storage_error"


class FulltextAcquisitionError(BaseModel):
    """不包含响应正文、Cookie 或临时签名 URL 的安全失败摘要。"""

    code: FulltextAcquisitionErrorCode
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool
    http_status_code: int | None = Field(default=None, ge=100, le=599)


class AcquiredFulltext(BaseModel):
    """已下载、校验并写入私有暂存区的 PDF 信息。"""

    candidate_id: UUID
    doi: str = Field(min_length=1, max_length=512)
    source_url: str = Field(min_length=1, max_length=5000)
    staging_object_key: str = Field(min_length=1, max_length=1024)
    original_filename: str = Field(min_length=1, max_length=512)
    media_type: str = Field(default="application/pdf")
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin_kind: str = Field(default="open_access")
    access_rights: str = Field(default="open_access")
    acquired_at: datetime


class FulltextAcquisitionResult(BaseModel):
    """全文获取的互斥结果，供 SSE、重试和后续入库服务使用。"""

    candidate_id: UUID
    status: FulltextAcquisitionStatus
    document: AcquiredFulltext | None = None
    error: FulltextAcquisitionError | None = None

    @model_validator(mode="after")
    def status_matches_payload(self) -> FulltextAcquisitionResult:
        """防止调用方将未校验文件或失败结果伪装成可入库正文。"""
        if self.status is FulltextAcquisitionStatus.AVAILABLE:
            if self.document is None or self.error is not None:
                raise ValueError("available 结果必须且只能携带已校验文件")
        elif self.document is not None or self.error is None:
            raise ValueError("非 available 结果必须且只能携带失败原因")

        return self
