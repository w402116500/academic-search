"""候选正式引用的 HTTP 响应和稳定领域错误契约。"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.literature.citation_formatter import CitationFormat


class CandidateCitationErrorCode(StrEnum):
    """候选正式引用渲染可以稳定返回给 API 的业务错误码。"""

    CANDIDATE_NOT_FOUND = "candidate_citation_not_found"
    SESSION_EXPIRED = "candidate_citation_session_expired"
    CITATION_NOT_READY = "candidate_citation_not_ready"


class CandidateCitationError(RuntimeError):
    """候选不存在、会话过期或题录尚不可安全格式化时抛出的明确错误。"""

    def __init__(self, code: CandidateCitationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class CandidateCitationResponse(BaseModel):
    """由后端从已核验格式中立题录渲染出的单个正式引用。"""

    candidate_id: UUID
    format: CitationFormat
    text: str = Field(min_length=1, description="可直接复制或导出的正式引用文本")
