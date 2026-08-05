"""SQLAlchemy adapter for research workspace persistence."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.collection import ResearchCollection
from app.modules.research.workspace_models import ResearchWorkspace
from app.modules.research.workspace_repository import (
    CreateResearchWorkspace,
    UpdateWorkspaceDetails,
    WorkspaceListFilter,
)


class SqlAlchemyWorkspaceRepository:
    """Persist and query workspaces while containing ORM-specific behavior."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, command: CreateResearchWorkspace) -> ResearchWorkspace:
        model = ResearchCollection(
            owner_user_id=command.owner_user_id,
            name=command.name,
            description=command.description,
            status="active",
        )
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        return _workspace_from_model(model)

    async def list_owned(self, query: WorkspaceListFilter) -> list[ResearchWorkspace]:
        filters = [
            ResearchCollection.owner_user_id == query.owner_user_id,
            ResearchCollection.status.in_(query.statuses),
        ]
        if query.query:
            name_pattern = f"%{_escape_like_pattern(query.query)}%"
            search_filters = [ResearchCollection.name.ilike(name_pattern, escape="\\")]
            if query.matching_workflow_stages:
                search_filters.append(
                    ResearchCollection.workflow_stage.in_(query.matching_workflow_stages)
                )
            filters.append(or_(*search_filters))

        if query.before_updated_at is not None and query.before_id is not None:
            filters.append(
                or_(
                    ResearchCollection.updated_at < query.before_updated_at,
                    and_(
                        ResearchCollection.updated_at == query.before_updated_at,
                        ResearchCollection.id < query.before_id,
                    ),
                )
            )

        result = await self._session.scalars(
            select(ResearchCollection)
            .where(*filters)
            .order_by(ResearchCollection.updated_at.desc(), ResearchCollection.id.desc())
            .limit(query.limit)
        )
        return [_workspace_from_model(model) for model in result]

    async def get_owned(
        self, *, owner_user_id: UUID, workspace_id: UUID
    ) -> ResearchWorkspace | None:
        model = await self._owned_model(owner_user_id=owner_user_id, workspace_id=workspace_id)
        return _workspace_from_model(model) if model is not None else None

    async def update_details(
        self,
        *,
        owner_user_id: UUID,
        workspace_id: UUID,
        changes: UpdateWorkspaceDetails,
    ) -> ResearchWorkspace:
        model = await self._required_owned_model(
            owner_user_id=owner_user_id, workspace_id=workspace_id
        )
        if changes.name is not None:
            model.name = changes.name
        if changes.change_description:
            model.description = changes.description
        await self._session.commit()
        await self._session.refresh(model)
        return _workspace_from_model(model)

    async def set_status(
        self, *, owner_user_id: UUID, workspace_id: UUID, status: str
    ) -> ResearchWorkspace:
        model = await self._required_owned_model(
            owner_user_id=owner_user_id, workspace_id=workspace_id
        )
        model.status = status
        await self._session.commit()
        await self._session.refresh(model)
        return _workspace_from_model(model)

    async def _owned_model(
        self, *, owner_user_id: UUID, workspace_id: UUID
    ) -> ResearchCollection | None:
        return await self._session.scalar(
            select(ResearchCollection).where(
                ResearchCollection.id == workspace_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status.in_(("active", "archived")),
            )
        )

    async def _required_owned_model(
        self, *, owner_user_id: UUID, workspace_id: UUID
    ) -> ResearchCollection:
        model = await self._owned_model(owner_user_id=owner_user_id, workspace_id=workspace_id)
        if model is None:
            raise LookupError("workspace disappeared during an owned command")
        return model


def _workspace_from_model(model: ResearchCollection) -> ResearchWorkspace:
    return ResearchWorkspace(
        id=model.id,
        owner_user_id=model.owner_user_id,
        name=model.name,
        description=model.description,
        research_question=model.research_question,
        status=model.status,
        workflow_stage=model.workflow_stage,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _escape_like_pattern(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
