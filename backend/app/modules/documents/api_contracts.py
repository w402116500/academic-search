"""候选全文 HTTP 响应和稳定领域错误契约。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from app.modules.documents.contracts import (
    AcquiredFulltext,
    FulltextAcquisitionError,
    FulltextAcquisitionStatus,
)


class CandidateFulltextErrorCode(StrEnum):
    """候选全文任务服务可以稳定返回给 API 的业务错误码。"""

    CANDIDATE_NOT_FOUND = "candidate_fulltext_not_found"
    CANDIDATE_NOT_ELIGIBLE = "candidate_fulltext_not_eligible"
    SEARCH_NOT_FINISHED = "candidate_fulltext_search_not_finished"
    SESSION_EXPIRED = "candidate_fulltext_session_expired"
    STATE_NOT_FOUND = "candidate_fulltext_state_not_found"
    NOT_RETRYABLE = "candidate_fulltext_not_retryable"
    UPLOAD_NOT_AUTHORIZED = "candidate_fulltext_upload_not_authorized"
    UPLOAD_IN_PROGRESS = "candidate_fulltext_upload_in_progress"


class CandidateFulltextError(RuntimeError):
    """候选不存在、会话过期或全文任务重试非法时抛出的明确异常。"""

    def __init__(self, code: CandidateFulltextErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class CandidateFulltextResponse(BaseModel):
    """单篇候选全文任务的短期状态；文件详情仅在 available 时出现。"""

    search_run_id: UUID
    candidate_id: UUID
    attempt_no: int
    status: FulltextAcquisitionStatus
    document: AcquiredFulltext | None = None
    error: FulltextAcquisitionError | None = None
    requested_at: datetime
    updated_at: datetime
