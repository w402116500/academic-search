"""Persistence port for the document ingestion pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.modules.rag.ingestion.contracts import (
    DocumentChunkDraft,
    IngestionContext,
    IngestionError,
    ParsedDocument,
    VectorChunk,
)


class IngestionRepository(Protocol):
    """入库编排器所需的持久化边界，单元测试可以替换为内存实现。"""

    async def claim(self, ingestion_run_id: UUID) -> IngestionContext | None: ...

    async def record_parse(self, ingestion_run_id: UUID, parsed: ParsedDocument) -> None: ...

    async def replace_chunks(
        self,
        ingestion_run_id: UUID,
        chunks: Sequence[DocumentChunkDraft],
        chunking_config: dict[str, int | str],
    ) -> None: ...

    async def load_vector_chunks(self, context: IngestionContext) -> tuple[VectorChunk, ...]: ...

    async def record_embedding(
        self,
        ingestion_run_id: UUID,
        embedding_config: dict[str, str | int],
        vector_dimension: int,
    ) -> None: ...

    async def complete(self, ingestion_run_id: UUID, indexed_l3_chunk_count: int) -> None: ...

    async def mark_failed(self, ingestion_run_id: UUID, error: IngestionError) -> None: ...
