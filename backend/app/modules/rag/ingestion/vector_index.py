"""Vector-index port owned by the RAG ingestion domain."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.modules.rag.ingestion.contracts import EmbeddedVectorChunk


class DocumentChunkVectorIndex(Protocol):
    """Persist and compensate L3 vectors without exposing Milvus."""

    async def upsert(self, chunks: Sequence[EmbeddedVectorChunk]) -> None: ...

    async def delete_ingestion_run(self, ingestion_run_id: UUID) -> None: ...
