"""Search-owned asynchronous task ports."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class SearchRunJobQueue(Protocol):
    """Enqueue a persisted search run without exposing the queue implementation."""

    async def enqueue_search(self, search_run_id: UUID) -> str:
        """Return the durable queue job identifier."""
        ...


class CandidateRelevanceJobQueue(Protocol):
    """Enqueue one relevance attempt for the complete candidate collection."""

    async def enqueue_relevance(self, *, search_run_id: UUID, attempt_no: int) -> str:
        """Return the idempotent queue job identifier."""
        ...


class SearchRunQueueError(RuntimeError):
    """The search run could not be accepted by the configured queue."""


class CandidateRelevanceQueueError(RuntimeError):
    """The relevance run could not be accepted by the configured queue."""
