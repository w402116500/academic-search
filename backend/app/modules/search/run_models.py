"""Search-run state owned by the search module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SearchWorkspace:
    """Workspace facts that fence search-run commands."""

    id: UUID
    owner_user_id: UUID
    status: str
    workflow_stage: str


@dataclass(frozen=True, slots=True)
class SearchRunRecord:
    """Durable search-run snapshot without ORM behavior."""

    id: UUID
    collection_id: UUID
    research_plan_id: UUID
    arq_job_id: str | None
    redis_session_key: str | None
    status: str
    stage: str
    attempt_no: int
    provider_summary: dict[str, Any]
    candidate_counts: dict[str, Any]
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SearchRunContext:
    """A search run together with its workspace fencing facts."""

    workspace: SearchWorkspace
    run: SearchRunRecord


@dataclass(frozen=True, slots=True)
class DailySearchRunCounts:
    user: int
    global_: int
