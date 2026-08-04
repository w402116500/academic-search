"""研究集合构建、文献列表与入库重试的 API 契约。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IngestionRunStatus(StrEnum):
    """入库运行写入数据库的稳定状态值。"""

    PENDING = "pending"  # 待确认：已加入集合，但尚未允许解析。
    QUEUED = "queued"  # 已投递：等待入库 Worker 领取。
    RUNNING = "running"  # 执行中：正在解析、切块、嵌入或写入索引。
    COMPLETED = "completed"  # 已完成：当前版本已可参与 RAG。
    FAILED = "failed"  # 失败：可根据失败原因重新投递新运行。
    CANCELLED = "cancelled"  # 已取消：用户在构建前从集合中移出文献。


class CollectionBuildErrorCode(StrEnum):
    """集合文献操作可安全展示给前端的失败类型。"""

    COLLECTION_NOT_FOUND = "collection_build_collection_not_found"
    DOCUMENT_NOT_FOUND = "collection_build_document_not_found"
    NO_PENDING_DOCUMENTS = "collection_build_no_pending_documents"
    DOCUMENT_NOT_PENDING = "collection_build_document_not_pending"
    RUN_NOT_FOUND = "collection_build_run_not_found"
    RUN_NOT_RETRYABLE = "collection_build_run_not_retryable"
    USER_QUOTA_EXCEEDED = "collection_build_user_quota_exceeded"
    GLOBAL_BUDGET_EXHAUSTED = "collection_build_global_budget_exhausted"


class CollectionBuildError(RuntimeError):
    """阻止集合构建、移除或重试的明确业务异常。"""

    def __init__(self, code: CollectionBuildErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class IngestionRunResponse(BaseModel):
    """单次入库运行的持久化状态，适合列表轮询和失败诊断。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    arq_job_id: str | None
    pipeline_version: str
    status: IngestionRunStatus
    stage: str
    parser_name: str | None
    parser_version: str | None
    chunking_config: dict[str, Any]
    embedding_config: dict[str, Any]
    statistics: dict[str, Any]
    error_code: str | None
    error_message: str | None
    attempt_no: int
    is_current: bool
    started_at: datetime | None
    finished_at: datetime | None
    submitted_at: datetime | None = None
    created_at: datetime


class CollectionDocumentResponse(BaseModel):
    """工作区中一篇活动文献及其最新入库运行。"""

    document_id: UUID
    paper_id: UUID
    doi: str
    title: str
    authors: list[dict[str, Any]]
    publication_year: int | None
    venue: str | None
    citation_text: str
    tags: list[str]
    note: str | None
    original_filename: str
    byte_size: int
    source_url: str | None
    access_rights: str
    added_at: datetime
    latest_ingestion_run: IngestionRunResponse | None


class CollectionIngestionSummary(BaseModel):
    """集合页面展示的任务数量与可问答文献数量。"""

    active_document_count: int
    researchable_document_count: int
    ingestion_status_counts: dict[IngestionRunStatus, int] = Field(default_factory=dict)


class CollectionDocumentsResponse(BaseModel):
    """活动文献列表与用于刷新页面的入库汇总。"""

    collection_id: UUID
    documents: list[CollectionDocumentResponse]
    summary: CollectionIngestionSummary


class CollectionBuildRunResponse(BaseModel):
    """一次确认构建中单个入库运行的投递结果。"""

    ingestion_run_id: UUID
    status: IngestionRunStatus
    arq_job_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class CollectionBuildResponse(BaseModel):
    """确认构建后的批量投递结果，允许部分文献投递失败。"""

    collection_id: UUID
    workflow_stage: str
    runs: list[CollectionBuildRunResponse]


class CollectionDocumentRemovalResponse(BaseModel):
    """移出待确认文献后的审计结果；文件不会被立即物理删除。"""

    document_id: UUID
    collection_paper_status: str
    ingestion_run_status: IngestionRunStatus
