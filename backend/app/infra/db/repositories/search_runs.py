"""SQLAlchemy adapter for durable search-run state."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.collection import ResearchCollection
from app.infra.db.models.workflow import ResearchPlan, SearchRun
from app.infra.db.repositories.research_plans import research_plan_from_model
from app.modules.research.plan_models import ResearchPlanRecord
from app.modules.research.state import ResearchPlanStatus
from app.modules.search.run_models import (
    DailySearchRunCounts,
    SearchRunContext,
    SearchRunRecord,
    SearchWorkspace,
)
from app.modules.search.run_repository import ActiveSearchRunConflict, CreateSearchRun
from app.modules.search.state import SearchRunStage, SearchRunStatus


class SqlAlchemySearchRunRepository:
    """Persist search runs while containing SQL locking and uniqueness behavior."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owned_workspace_for_update(
        self, *, owner_user_id: UUID, collection_id: UUID
    ) -> SearchWorkspace | None:
        model = await self._session.scalar(
            select(ResearchCollection)
            .where(
                ResearchCollection.id == collection_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status.in_(("active", "archived")),
            )
            .with_for_update()
        )
        return _workspace_from_model(model) if model is not None else None

    async def get_confirmed_plan_for_update(
        self, *, collection_id: UUID
    ) -> ResearchPlanRecord | None:
        plan = await self._session.scalar(
            select(ResearchPlan)
            .where(
                ResearchPlan.collection_id == collection_id,
                ResearchPlan.status == ResearchPlanStatus.CONFIRMED.value,
            )
            .order_by(ResearchPlan.revision.desc())
            .limit(1)
            .with_for_update()
        )
        return research_plan_from_model(plan) if plan is not None else None

    async def get_current_run(
        self, *, owner_user_id: UUID, collection_id: UUID
    ) -> SearchRunRecord | None:
        run = await self._session.scalar(
            select(SearchRun)
            .join(ResearchCollection, ResearchCollection.id == SearchRun.collection_id)
            .where(
                SearchRun.collection_id == collection_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status.in_(("active", "archived")),
            )
            .order_by(SearchRun.created_at.desc())
            .limit(1)
        )
        return _run_from_model(run) if run is not None else None

    async def get_owned_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        for_update: bool = False,
    ) -> SearchRunRecord | None:
        statement = (
            select(SearchRun)
            .join(ResearchCollection, ResearchCollection.id == SearchRun.collection_id)
            .where(
                SearchRun.id == search_run_id,
                SearchRun.collection_id == collection_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status.in_(("active", "archived")),
            )
        )
        if for_update:
            statement = statement.with_for_update()
        run = await self._session.scalar(statement)
        return _run_from_model(run) if run is not None else None

    async def has_active_run(self, research_plan_id: UUID) -> bool:
        run_id = await self._session.scalar(
            select(SearchRun.id).where(
                SearchRun.research_plan_id == research_plan_id,
                SearchRun.status.in_((SearchRunStatus.QUEUED.value, SearchRunStatus.RUNNING.value)),
            )
        )
        return run_id is not None

    async def count_since(
        self, *, owner_user_id: UUID, period_start: datetime
    ) -> DailySearchRunCounts:
        user_count = int(
            await self._session.scalar(
                select(func.count(SearchRun.id))
                .join(ResearchCollection, ResearchCollection.id == SearchRun.collection_id)
                .where(
                    ResearchCollection.owner_user_id == owner_user_id,
                    SearchRun.created_at >= period_start,
                )
            )
            or 0
        )
        global_count = int(
            await self._session.scalar(
                select(func.count(SearchRun.id)).where(SearchRun.created_at >= period_start)
            )
            or 0
        )
        return DailySearchRunCounts(user=user_count, global_=global_count)

    async def create_run(
        self, *, workspace: SearchWorkspace, command: CreateSearchRun
    ) -> SearchRunContext:
        workspace_model = await self._required_workspace(workspace.id)
        _apply_workspace(workspace_model, workspace)
        run = SearchRun(
            id=command.run_id,
            collection_id=command.collection_id,
            research_plan_id=command.research_plan_id,
            redis_session_key=command.redis_session_key,
            status=SearchRunStatus.QUEUED.value,
            stage=SearchRunStage.DISPATCH.value,
            attempt_no=command.attempt_no,
            provider_summary={},
            candidate_counts={},
        )
        self._session.add(run)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ActiveSearchRunConflict from exc
        await self._session.refresh(workspace_model)
        await self._session.refresh(run)
        return SearchRunContext(_workspace_from_model(workspace_model), _run_from_model(run))

    async def get_run_context_for_update(self, search_run_id: UUID) -> SearchRunContext | None:
        row = (
            await self._session.execute(
                select(SearchRun, ResearchCollection)
                .join(ResearchCollection, ResearchCollection.id == SearchRun.collection_id)
                .where(SearchRun.id == search_run_id)
                .with_for_update(of=(SearchRun, ResearchCollection))
            )
        ).one_or_none()
        if row is None:
            return None
        run, workspace = row._tuple()
        return SearchRunContext(_workspace_from_model(workspace), _run_from_model(run))

    async def get_relevance_run_for_update(self, search_run_id: UUID) -> SearchRunRecord | None:
        run = await self._session.scalar(
            select(SearchRun).where(SearchRun.id == search_run_id).with_for_update(skip_locked=True)
        )
        return _run_from_model(run) if run is not None else None

    async def get_plan(self, research_plan_id: UUID) -> ResearchPlanRecord | None:
        plan = await self._session.get(ResearchPlan, research_plan_id)
        return research_plan_from_model(plan) if plan is not None else None

    async def save(self, context: SearchRunContext) -> SearchRunContext:
        workspace = await self._required_workspace(context.workspace.id)
        run = await self._required_run(context.run.id)
        _apply_workspace(workspace, context.workspace)
        _apply_run(run, context.run)
        await self._session.commit()
        await self._session.refresh(workspace)
        await self._session.refresh(run)
        return SearchRunContext(_workspace_from_model(workspace), _run_from_model(run))

    async def _required_workspace(self, workspace_id: UUID) -> ResearchCollection:
        workspace = await self._session.get(ResearchCollection, workspace_id)
        if workspace is None:
            raise LookupError("research workspace disappeared during a search-run command")
        return workspace

    async def _required_run(self, search_run_id: UUID) -> SearchRun:
        run = await self._session.get(SearchRun, search_run_id)
        if run is None:
            raise LookupError("search run disappeared during a search-run command")
        return run


def _workspace_from_model(model: ResearchCollection) -> SearchWorkspace:
    return SearchWorkspace(
        id=model.id,
        owner_user_id=model.owner_user_id,
        status=model.status,
        workflow_stage=model.workflow_stage,
    )


def _run_from_model(model: SearchRun) -> SearchRunRecord:
    return SearchRunRecord(
        id=model.id,
        collection_id=model.collection_id,
        research_plan_id=model.research_plan_id,
        arq_job_id=model.arq_job_id,
        redis_session_key=model.redis_session_key,
        status=model.status,
        stage=model.stage,
        attempt_no=model.attempt_no,
        provider_summary=model.provider_summary,
        candidate_counts=model.candidate_counts,
        error_code=model.error_code,
        error_message=model.error_message,
        started_at=model.started_at,
        finished_at=model.finished_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _apply_workspace(model: ResearchCollection, workspace: SearchWorkspace) -> None:
    model.status = workspace.status
    model.workflow_stage = workspace.workflow_stage


def _apply_run(model: SearchRun, run: SearchRunRecord) -> None:
    model.arq_job_id = run.arq_job_id
    model.redis_session_key = run.redis_session_key
    model.status = run.status
    model.stage = run.stage
    model.attempt_no = run.attempt_no
    model.provider_summary = run.provider_summary
    model.candidate_counts = run.candidate_counts
    model.error_code = run.error_code
    model.error_message = run.error_message
    model.started_at = run.started_at
    model.finished_at = run.finished_at
