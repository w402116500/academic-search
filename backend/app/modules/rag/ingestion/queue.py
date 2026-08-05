"""RAG ingestion asynchronous task port."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class IngestionJobQueue(Protocol):
    """Enqueue one persisted document ingestion run."""

    async def enqueue_ingestion(self, ingestion_run_id: UUID) -> str:
        """Return the idempotent queue job identifier."""
        ...


class IngestionQueueError(RuntimeError):
    """The ingestion run could not be accepted by the configured queue."""
