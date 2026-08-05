"""Research execution context and persistence port owned by the research module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.modules.rag.retrieval import RetrievalScope, RetrievedEvidence
from app.modules.research.contracts import ResearchRunStage, ResearchRunStatus


@dataclass(frozen=True, slots=True)
class ResearchExecutionContext:
    research_run_id: UUID
    conversation_id: UUID
    collection_id: UUID
    owner_user_id: UUID
    question: str
    mode: str
    langgraph_thread_id: str
    model_config: dict[str, Any]

    @property
    def retrieval_scope(self) -> RetrievalScope:
        return RetrievalScope(owner_user_id=self.owner_user_id, collection_id=self.collection_id)


class ResearchOutcome(Protocol):
    @property
    def status(self) -> ResearchRunStatus: ...

    @property
    def stage(self) -> ResearchRunStage: ...

    @property
    def answer(self) -> str: ...

    @property
    def evidences(self) -> tuple[RetrievedEvidence, ...]: ...

    @property
    def cited_chunk_ids(self) -> tuple[UUID, ...]: ...

    @property
    def retrieval_trace(self) -> dict[str, Any]: ...

    @property
    def mode(self) -> str: ...


class ResearchExecutionPort(Protocol):
    async def claim(self, research_run_id: UUID) -> ResearchExecutionContext | None: ...

    async def set_stage(self, research_run_id: UUID, stage: ResearchRunStage) -> bool: ...

    async def is_cancel_requested(self, research_run_id: UUID) -> bool: ...

    async def finalize_cancellation(self, research_run_id: UUID) -> bool: ...

    async def complete(
        self, research_run_id: UUID, outcome: ResearchOutcome
    ) -> ResearchRunStatus | None: ...

    async def fail(
        self, research_run_id: UUID, *, code: str, message: str
    ) -> ResearchRunStatus | None: ...
