"""检索运行、进度和候选审核的 HTTP 契约。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing_extensions import TypedDict

from app.modules.documents.api_contracts import CandidateFulltextResponse
from app.modules.documents.contracts import FulltextAcquisitionStatus
from app.modules.search.contracts import ProviderError, UnifiedCandidate
from app.modules.search.state import SearchRunStage, SearchRunStatus


class SearchRunErrorCode(StrEnum):
    """检索运行服务可以稳定返回给 API 的业务错误码。"""

    COLLECTION_NOT_FOUND = "search_run_collection_not_found"
    COLLECTION_NOT_ACTIVE = "search_run_collection_not_active"
    PLAN_NOT_CONFIRMED = "search_run_plan_not_confirmed"
    ACTIVE_RUN_EXISTS = "search_run_active_exists"
    RUN_NOT_FOUND = "search_run_not_found"
    RUN_NOT_RETRYABLE = "search_run_not_retryable"
    QUEUE_UNAVAILABLE = "search_run_queue_unavailable"
    SESSION_EXPIRED = "search_run_session_expired"
    PLAN_DATA_INVALID = "search_run_plan_data_invalid"
    USER_QUOTA_EXCEEDED = "search_run_user_quota_exceeded"
    GLOBAL_BUDGET_EXHAUSTED = "search_run_global_budget_exhausted"


class SearchRunError(RuntimeError):
    """检索运行前置条件、队列或短期会话不满足时抛出的明确异常。"""

    def __init__(self, code: SearchRunErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProviderSummary(TypedDict, total=False):
    """Stable provider-level progress fields without inventing absent values."""

    status: str
    candidate_count: int
    query_count: int
    result_count: int
    raw_candidate_count: int
    error: str
    errors: list[ProviderError]


class CandidateCounts(TypedDict, total=False):
    """Known current and legacy search counters without synthesizing missing keys."""

    raw_candidate_count: int
    deduplicated_candidate_count: int
    included_candidate_count: int
    candidate_count: int
    excluded_candidate_count: int
    relevance_total_count: int
    relevance_analyzed_count: int
    relevance_excluded_count: int
    relevance_pending_count: int
    relevance_completed_count: int
    relevance_insufficient_count: int
    relevance_failed_count: int
    screening_candidate_count: int
    citation_enriched_count: int
    pdf_available_count: int
    pdf_requires_upload_count: int
    included: int
    total: int


class SearchRunResponse(BaseModel):
    """检索运行的长期状态和摘要；候选全文详情不在该响应中长期保存。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    collection_id: UUID
    research_plan_id: UUID
    status: SearchRunStatus
    stage: SearchRunStage
    attempt_no: int
    provider_summary: dict[str, ProviderSummary]
    candidate_counts: CandidateCounts
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SearchCandidatesResponse(BaseModel):
    """检索候选快照响应；候选来源于 Redis 短期会话。"""

    run_id: UUID
    status: SearchRunStatus
    candidate_counts: CandidateCounts
    candidates: list[UnifiedCandidate]


class SearchProgressEvent(BaseModel):
    """通过 SSE 暴露的可验证检索进度，不包含模型思维过程。"""

    run_id: UUID
    status: SearchRunStatus
    stage: SearchRunStage
    provider_summary: dict[str, ProviderSummary] = Field(default_factory=dict)
    candidate_counts: CandidateCounts = Field(default_factory=CandidateCounts)
    message: str | None = None


class CandidateReviewFilter(StrEnum):
    """候选审核页允许的服务端筛选值，避免前端自行推断审核状态。"""

    ALL = "all"
    CHINESE = "zh"
    ENGLISH = "en"
    PRIORITY = "priority"
    BACKGROUND = "background"
    AVAILABLE = "available"
    OPEN_ACCESS = "open_access"
    HAS_DOI = "doi"
    SELECTED = "selected"


class CandidateSelectionRequest(BaseModel):
    """准备清单的增删请求，只允许提交当前候选的临时 UUID。"""

    candidate_ids: list[UUID] = Field(min_length=1, max_length=50)
    selected: bool

    @field_validator("candidate_ids")
    @classmethod
    def require_unique_candidate_ids(cls, value: list[UUID]) -> list[UUID]:
        """重复候选会掩盖客户端状态错误，因此拒绝而不是静默去重。"""
        if len(set(value)) != len(value):
            raise ValueError("候选标识不能重复")
        return value


class CandidateSelectionResponse(BaseModel):
    """准备清单更新后的最小摘要，前端随后重新读取当前分页。"""

    run_id: UUID
    selected_count: int = Field(ge=0)


class CandidateSelectionSummary(BaseModel):
    """只统计本次准备清单，和待确认研究集合的持久数量严格分开。"""

    selected_count: int = Field(ge=0)
    needs_fulltext_count: int = Field(ge=0)
    fulltext_in_progress_count: int = Field(ge=0)
    ready_for_admission_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)


class SearchCandidateReviewItem(BaseModel):
    """候选审核表的一行服务端视图，包含选择与全文短期状态。"""

    candidate: UnifiedCandidate
    is_selected: bool
    fulltext: CandidateFulltextResponse | None = None


class SearchCandidatePageInfo(BaseModel):
    """稳定游标分页信息；前端保存已访问游标以支持上一页。"""

    limit: int = Field(ge=1, le=50)
    total: int = Field(ge=0)
    next_cursor: str | None = None


class SearchCandidatePageResponse(BaseModel):
    """候选审核页专用响应，不再向浏览器发送未查看的所有候选。"""

    run_id: UUID
    status: SearchRunStatus
    candidate_counts: CandidateCounts
    items: list[SearchCandidateReviewItem]
    page: SearchCandidatePageInfo
    selection: CandidateSelectionSummary


class CandidatePreparationItem(BaseModel):
    """一次批量全文准备中单篇候选的可展示结果。"""

    candidate_id: UUID
    status: FulltextAcquisitionStatus | None = None
    message: str
    retryable: bool = False


class CandidatePreparationBatchResponse(BaseModel):
    """批量投递全文核验后的逐项结果，不把队列成功误表示为全文成功。"""

    run_id: UUID
    selected_count: int = Field(ge=0)
    queued_count: int = Field(ge=0)
    items: list[CandidatePreparationItem]


class CandidateAdmissionItem(BaseModel):
    """一次批量加入待确认集合中单篇候选的准入结果。"""

    candidate_id: UUID
    status: str = Field(min_length=1, max_length=64)
    message: str
    retryable: bool = False


class CandidateAdmissionBatchResponse(BaseModel):
    """批量准入结果；成功项会从短期准备清单移除，其余项目保留供继续处理。"""

    run_id: UUID
    selected_count: int = Field(ge=0)
    admitted_count: int = Field(ge=0)
    already_joined_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    items: list[CandidateAdmissionItem]
