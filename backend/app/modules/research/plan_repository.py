"""Research-plan persistence commands and owner-side port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.modules.research.plan_models import ResearchPlanContext, ResearchPlanRecord


@dataclass(frozen=True, slots=True)
class CreateInitialResearchPlan:
    workspace_id: UUID
    plan_id: UUID
    owner_user_id: UUID
    workspace_name: str
    raw_request: str


@dataclass(frozen=True, slots=True)
class CreateResearchPlanRevision:
    plan_id: UUID
    collection_id: UUID
    revision: int
    raw_request: str


class ResearchPlanRepository(Protocol):
    """Persistence boundary for workspace-scoped research-plan versions."""

    async def create_initial(self, command: CreateInitialResearchPlan) -> ResearchPlanContext: ...

    async def get_current(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        for_update: bool = False,
    ) -> ResearchPlanContext | None: ...

    async def add_revision(
        self,
        *,
        current: ResearchPlanContext,
        command: CreateResearchPlanRevision,
    ) -> ResearchPlanContext: ...

    async def get_generating(self, plan_id: UUID) -> ResearchPlanRecord | None: ...

    async def get_by_id_for_update(self, plan_id: UUID) -> ResearchPlanContext | None: ...

    async def save(self, context: ResearchPlanContext) -> ResearchPlanContext: ...
