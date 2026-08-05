"""Collections-owned workspace persistence commands and port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.modules.research.workspace_models import ResearchWorkspace


@dataclass(frozen=True, slots=True)
class CreateResearchWorkspace:
    owner_user_id: UUID
    name: str
    description: str | None


@dataclass(frozen=True, slots=True)
class WorkspaceListFilter:
    owner_user_id: UUID
    statuses: tuple[str, ...]
    query: str | None
    matching_workflow_stages: tuple[str, ...]
    before_updated_at: datetime | None
    before_id: UUID | None
    limit: int


@dataclass(frozen=True, slots=True)
class UpdateWorkspaceDetails:
    name: str | None
    description: str | None
    change_description: bool


class WorkspaceRepository(Protocol):
    """Persistence port for workspace ownership and lifecycle facts."""

    async def create(self, command: CreateResearchWorkspace) -> ResearchWorkspace: ...

    async def list_owned(self, query: WorkspaceListFilter) -> list[ResearchWorkspace]: ...

    async def get_owned(
        self, *, owner_user_id: UUID, workspace_id: UUID
    ) -> ResearchWorkspace | None: ...

    async def update_details(
        self,
        *,
        owner_user_id: UUID,
        workspace_id: UUID,
        changes: UpdateWorkspaceDetails,
    ) -> ResearchWorkspace: ...

    async def set_status(
        self, *, owner_user_id: UUID, workspace_id: UUID, status: str
    ) -> ResearchWorkspace: ...
