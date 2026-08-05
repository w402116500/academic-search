"""Research-owned progress event persistence port."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.research.contracts import ResearchProgressEvent


def build_research_event_stream_key(research_run_id: UUID) -> str:
    """Return the stable Redis-compatible stream key for a research run."""
    return f"research:run:{research_run_id}:events"


class ResearchEventStore(Protocol):
    """Publish and replay public research progress events."""

    async def publish(self, event: ResearchProgressEvent) -> str: ...

    async def read_events(
        self,
        research_run_id: UUID,
        *,
        last_event_id: str,
        block_milliseconds: int = 5_000,
    ) -> tuple[tuple[str, dict[str, object]], ...]: ...
