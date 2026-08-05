"""RAG 文献入库链路共享的稳定数据契约与错误类型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID


class IngestionErrorCode(StrEnum):
    """入库失败的机器可识别代码，会持久化到 ``ingestion_runs``。"""

    INGESTION_RUN_NOT_FOUND = "ingestion_run_not_found"
    INGESTION_ALREADY_RUNNING = "ingestion_already_running"
    COLLECTION_UNAVAILABLE = "collection_unavailable"
    STORAGE_READ_FAILED = "storage_read_failed"
    PDF_PARSE_FAILED = "pdf_parse_failed"
    PDF_NO_EXTRACTABLE_TEXT = "pdf_no_extractable_text"
    CHUNKING_FAILED = "chunking_failed"
    EMBEDDING_FAILED = "embedding_failed"
    EMBEDDING_MISMATCH = "embedding_mismatch"
    VECTOR_INDEX_FAILED = "vector_index_failed"
    PERSISTENCE_FAILED = "persistence_failed"
    INVALID_TASK_PAYLOAD = "invalid_task_payload"
    UNEXPECTED = "unexpected"


class IngestionError(RuntimeError):
    """携带稳定错误码和是否可重试语义的入库异常。"""

    def __init__(
        self,
        code: IngestionErrorCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """从 PDF 中提取的单页文本，页码保持为用户可见的 1 起始编号。"""

    page_number: int
    text: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """解析器输出，保留空白页信息以便后续展示质量提示。"""

    parser_name: str
    parser_version: str
    total_pages: int
    pages: tuple[ParsedPage, ...]
    empty_page_numbers: tuple[int, ...]

    @property
    def text_page_count(self) -> int:
        """返回实际包含可提取文本的页数。"""
        return len(self.pages)

    def statistics(self) -> dict[str, Any]:
        """生成可安全写入 PostgreSQL JSONB 字段的解析统计。"""
        return {
            "total_pages": self.total_pages,
            "text_page_count": self.text_page_count,
            "empty_page_numbers": list(self.empty_page_numbers),
        }


@dataclass(frozen=True, slots=True)
class DocumentChunkDraft:
    """尚未写入 PostgreSQL 的分层原文块。"""

    id: UUID
    parent_chunk_id: UUID | None
    root_chunk_id: UUID
    level: int
    ordinal: int
    content: str
    token_count: int
    page_start: int
    page_end: int
    section_path: tuple[str, ...] | None
    locator: dict[str, Any]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class IngestionContext:
    """Worker 处理单次入库时需要的权限、版本与对象定位信息。"""

    ingestion_run_id: UUID
    owner_user_id: UUID
    collection_id: UUID
    document_id: UUID
    object_key: str
    retrying: bool = False


@dataclass(frozen=True, slots=True)
class VectorChunk:
    """L3 块写入 Milvus 前所需的最小业务过滤字段。"""

    chunk_id: UUID
    owner_user_id: UUID
    collection_id: UUID
    document_id: UUID
    ingestion_run_id: UUID
    level: int
    content: str


@dataclass(frozen=True, slots=True)
class EmbeddedVectorChunk:
    """已完成 embedding 的 L3 片段，向量维度由嵌入提供方实际返回。"""

    chunk: VectorChunk
    embedding: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    """Worker 成功返回的结构化结果，方便 API 或运维脚本记录任务结果。"""

    ingestion_run_id: UUID
    status: str
    indexed_l3_chunk_count: int
    temporary_file_path: Path | None = None
