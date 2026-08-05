"""Research-plan state owned by the research workflow domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ResearchPlanWorkspace:
    """Workspace facts needed while creating and versioning research plans."""

    id: UUID
    owner_user_id: UUID
    name: str
    description: str | None
    research_question: str | None
    status: str
    workflow_stage: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ResearchPlanRecord:
    """Durable research-plan snapshot without ORM behavior."""

    id: UUID
    collection_id: UUID
    revision: int
    raw_request: str
    status: str
    direction_options: list[dict[str, Any]]
    selected_direction_id: str | None
    scope: dict[str, Any]
    query_plan: dict[str, Any]
    model_snapshot: dict[str, Any]
    arq_job_id: str | None
    error_code: str | None
    error_message: str | None
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ResearchPlanContext:
    """A plan together with the workspace facts that fence its mutations."""

    workspace: ResearchPlanWorkspace
    plan: ResearchPlanRecord
