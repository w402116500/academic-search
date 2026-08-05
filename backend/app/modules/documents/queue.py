"""Document-owned asynchronous task ports."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class CandidateFulltextJobQueue(Protocol):
    """Enqueue full-text preparation for one search candidate."""

    async def enqueue_fulltext(
        self,
        *,
        search_run_id: UUID,
        candidate_id: UUID,
        attempt_no: int,
    ) -> str:
        """Return the idempotent queue job identifier."""
        ...


class CandidateFulltextQueueError(RuntimeError):
    """The full-text task could not be accepted by the configured queue."""
