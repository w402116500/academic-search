"""Search-run persistence commands and owner-side port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.modules.research.plan_models import ResearchPlanRecord
from app.modules.search.run_models import (
    DailySearchRunCounts,
    SearchRunContext,
    SearchRunRecord,
    SearchWorkspace,
)


class ActiveSearchRunConflict(RuntimeError):
    """The database active-run fence rejected a concurrent submission."""


@dataclass(frozen=True, slots=True)
class CreateSearchRun:
    run_id: UUID
    collection_id: UUID
    research_plan_id: UUID
    redis_session_key: str
    attempt_no: int


class SearchRunRepository(Protocol):
    """Persistence boundary shared by search commands and candidate consumers."""

    async def get_owned_workspace_for_update(
        self, *, owner_user_id: UUID, collection_id: UUID
    ) -> SearchWorkspace | None: ...

    async def get_confirmed_plan_for_update(
        self, *, collection_id: UUID
    ) -> ResearchPlanRecord | None: ...

    async def get_current_run(
        self, *, owner_user_id: UUID, collection_id: UUID
    ) -> SearchRunRecord | None: ...

    async def get_owned_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        for_update: bool = False,
    ) -> SearchRunRecord | None: ...

    async def has_active_run(self, research_plan_id: UUID) -> bool: ...

    async def count_since(
        self, *, owner_user_id: UUID, period_start: datetime
    ) -> DailySearchRunCounts: ...

    async def create_run(
        self, *, workspace: SearchWorkspace, command: CreateSearchRun
    ) -> SearchRunContext: ...

    async def get_run_context_for_update(self, search_run_id: UUID) -> SearchRunContext | None: ...

    async def get_relevance_run_for_update(self, search_run_id: UUID) -> SearchRunRecord | None: ...

    async def get_plan(self, research_plan_id: UUID) -> ResearchPlanRecord | None: ...

    async def save(self, context: SearchRunContext) -> SearchRunContext: ...
