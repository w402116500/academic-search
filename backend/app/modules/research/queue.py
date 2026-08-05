"""Research-owned asynchronous task ports."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class ResearchPlanJobQueue(Protocol):
    """Enqueue analysis for a persisted research plan."""

    async def enqueue_analysis(self, research_plan_id: UUID) -> str:
        """Return the idempotent queue job identifier."""
        ...


class ResearchJobQueue(Protocol):
    """Enqueue a persisted research conversation run."""

    async def enqueue_research(self, research_run_id: UUID, *, retry: bool = False) -> str:
        """Return the queue job identifier for the requested attempt."""
        ...


class ResearchPlanQueueError(RuntimeError):
    """The research-plan analysis could not be accepted by the configured queue."""


class ResearchQueueError(RuntimeError):
    """The research conversation run could not be accepted by the configured queue."""
