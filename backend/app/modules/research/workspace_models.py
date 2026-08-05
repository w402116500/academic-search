"""Research workspace state owned by the collections module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.modules.research.state import get_workflow_stage_presentation


@dataclass(frozen=True, slots=True)
class ResearchWorkspace:
    """Durable workspace facts without ORM or presentation-layer dependencies."""

    id: UUID
    owner_user_id: UUID
    name: str
    description: str | None
    research_question: str | None
    status: str
    workflow_stage: str
    created_at: datetime
    updated_at: datetime

    @property
    def workflow_stage_display(self) -> dict[str, str]:
        presentation = get_workflow_stage_presentation(self.workflow_stage)
        return {"label": presentation.label, "description": presentation.description}
