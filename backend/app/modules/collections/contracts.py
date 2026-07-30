"""研究集合文献准入服务的输入校验错误与稳定结果。"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class CollectionAdmissionErrorCode(StrEnum):
    """加入研究集合时可安全返回给 API 和前端的失败类别。"""

    COLLECTION_UNAVAILABLE = "collection_unavailable"
    CITATION_NOT_READY = "citation_not_ready"
    FULLTEXT_UNAVAILABLE = "fulltext_unavailable"
    FULLTEXT_MISMATCH = "fulltext_mismatch"
    DUPLICATE_DOCUMENT = "duplicate_document"
    STORAGE_ERROR = "storage_error"
    PERSISTENCE_ERROR = "persistence_error"


class CollectionAdmissionError(RuntimeError):
    """阻止不完整候选进入长期研究库的明确业务异常。"""

    def __init__(
        self,
        code: CollectionAdmissionErrorCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class CollectionAdmissionStatus(StrEnum):
    """文献加入集合后的幂等结果。"""

    ADDED = "added"
    ALREADY_JOINED = "already_joined"


class CollectionAdmissionResult(BaseModel):
    """加入成功或幂等命中后，供 API 与后续 Worker 使用的标识集合。"""

    status: CollectionAdmissionStatus
    collection_id: UUID
    paper_id: UUID
    document_id: UUID | None = None
    ingestion_run_id: UUID | None = None
