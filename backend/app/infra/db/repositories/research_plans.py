"""SQLAlchemy adapter for versioned research-plan persistence."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.collection import ResearchCollection
from app.infra.db.models.workflow import ResearchPlan as ResearchPlanModel
from app.modules.research.plan_models import (
    ResearchPlanContext,
    ResearchPlanRecord,
    ResearchPlanWorkspace,
)
from app.modules.research.plan_repository import (
    CreateInitialResearchPlan,
    CreateResearchPlanRevision,
)
from app.modules.research.state import ResearchPlanStatus, WorkspaceWorkflowStage


class SqlAlchemyResearchPlanRepository:
    """Persist plan versions while keeping SQLAlchemy outside business services."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_initial(self, command: CreateInitialResearchPlan) -> ResearchPlanContext:
        workspace = ResearchCollection(
            id=command.workspace_id,
            owner_user_id=command.owner_user_id,
            name=command.workspace_name,
            research_question=command.raw_request,
            status="active",
            workflow_stage=WorkspaceWorkflowStage.ANALYZING.value,
        )
        plan = ResearchPlanModel(
            id=command.plan_id,
            collection_id=command.workspace_id,
            revision=1,
            raw_request=command.raw_request,
            status=ResearchPlanStatus.GENERATING.value,
            direction_options=[],
            scope={},
            query_plan={},
            model_snapshot={},
        )
        self._session.add_all((workspace, plan))
        await self._session.commit()
        await self._session.refresh(workspace)
        await self._session.refresh(plan)
        return _context_from_models(workspace, plan)

    async def get_current(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        for_update: bool = False,
    ) -> ResearchPlanContext | None:
        statement = (
            select(ResearchCollection, ResearchPlanModel)
            .join(ResearchPlanModel, ResearchPlanModel.collection_id == ResearchCollection.id)
            .where(
                ResearchCollection.id == collection_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status.in_(("active", "archived")),
            )
            .order_by(ResearchPlanModel.revision.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update(of=(ResearchCollection, ResearchPlanModel))
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        workspace, plan = row._tuple()
        return _context_from_models(workspace, plan)

    async def add_revision(
        self,
        *,
        current: ResearchPlanContext,
        command: CreateResearchPlanRevision,
    ) -> ResearchPlanContext:
        workspace = await self._required_workspace(current.workspace.id)
        previous_plan = await self._required_plan(current.plan.id)
        _apply_workspace(workspace, current.workspace)
        _apply_plan(previous_plan, current.plan)
        new_plan = ResearchPlanModel(
            id=command.plan_id,
            collection_id=command.collection_id,
            revision=command.revision,
            raw_request=command.raw_request,
            status=ResearchPlanStatus.GENERATING.value,
            direction_options=[],
            scope={},
            query_plan={},
            model_snapshot={},
        )
        self._session.add(new_plan)
        await self._session.commit()
        await self._session.refresh(workspace)
        await self._session.refresh(new_plan)
        return _context_from_models(workspace, new_plan)

    async def get_generating(self, plan_id: UUID) -> ResearchPlanRecord | None:
        plan = await self._session.scalar(
            select(ResearchPlanModel).where(
                ResearchPlanModel.id == plan_id,
                ResearchPlanModel.status == ResearchPlanStatus.GENERATING.value,
            )
        )
        return research_plan_from_model(plan) if plan is not None else None

    async def get_by_id_for_update(self, plan_id: UUID) -> ResearchPlanContext | None:
        row = (
            await self._session.execute(
                select(ResearchCollection, ResearchPlanModel)
                .join(ResearchPlanModel, ResearchPlanModel.collection_id == ResearchCollection.id)
                .where(ResearchPlanModel.id == plan_id)
                .with_for_update(of=(ResearchCollection, ResearchPlanModel))
            )
        ).one_or_none()
        if row is None:
            return None
        workspace, plan = row._tuple()
        return _context_from_models(workspace, plan)

    async def save(self, context: ResearchPlanContext) -> ResearchPlanContext:
        workspace = await self._required_workspace(context.workspace.id)
        plan = await self._required_plan(context.plan.id)
        _apply_workspace(workspace, context.workspace)
        _apply_plan(plan, context.plan)
        await self._session.commit()
        await self._session.refresh(workspace)
        await self._session.refresh(plan)
        return _context_from_models(workspace, plan)

    async def _required_workspace(self, workspace_id: UUID) -> ResearchCollection:
        workspace = await self._session.get(ResearchCollection, workspace_id)
        if workspace is None:
            raise LookupError("research workspace disappeared during a plan command")
        return workspace

    async def _required_plan(self, plan_id: UUID) -> ResearchPlanModel:
        plan = await self._session.get(ResearchPlanModel, plan_id)
        if plan is None:
            raise LookupError("research plan disappeared during a plan command")
        return plan


def _context_from_models(
    workspace: ResearchCollection,
    plan: ResearchPlanModel,
) -> ResearchPlanContext:
    return ResearchPlanContext(
        workspace=_workspace_from_model(workspace),
        plan=research_plan_from_model(plan),
    )


def _workspace_from_model(model: ResearchCollection) -> ResearchPlanWorkspace:
    return ResearchPlanWorkspace(
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


def research_plan_from_model(model: ResearchPlanModel) -> ResearchPlanRecord:
    return ResearchPlanRecord(
        id=model.id,
        collection_id=model.collection_id,
        revision=model.revision,
        raw_request=model.raw_request,
        status=model.status,
        direction_options=model.direction_options,
        selected_direction_id=model.selected_direction_id,
        scope=model.scope,
        query_plan=model.query_plan,
        model_snapshot=model.model_snapshot,
        arq_job_id=model.arq_job_id,
        error_code=model.error_code,
        error_message=model.error_message,
        confirmed_at=model.confirmed_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _apply_workspace(model: ResearchCollection, workspace: ResearchPlanWorkspace) -> None:
    model.name = workspace.name
    model.description = workspace.description
    model.research_question = workspace.research_question
    model.status = workspace.status
    model.workflow_stage = workspace.workflow_stage


def _apply_plan(model: ResearchPlanModel, plan: ResearchPlanRecord) -> None:
    model.raw_request = plan.raw_request
    model.status = plan.status
    model.direction_options = plan.direction_options
    model.selected_direction_id = plan.selected_direction_id
    model.scope = plan.scope
    model.query_plan = plan.query_plan
    model.model_snapshot = plan.model_snapshot
    model.arq_job_id = plan.arq_job_id
    model.error_code = plan.error_code
    model.error_message = plan.error_message
    model.confirmed_at = plan.confirmed_at
