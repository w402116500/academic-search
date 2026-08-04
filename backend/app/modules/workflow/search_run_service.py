"""多源文献检索运行的事务服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.db.models.collection import ResearchCollection
from app.db.models.workflow import ResearchPlan, SearchRun
from app.modules.workflow.contracts import SearchRunError, SearchRunErrorCode
from app.modules.workflow.job_queue import SearchRunJobQueue, SearchRunQueueError
from app.modules.workflow.search_session import build_search_session_key
from app.modules.workflow.settings import WorkflowSettings, get_workflow_settings
from app.modules.workflow.state import (
    ResearchPlanStatus,
    SearchRunStage,
    SearchRunStatus,
    WorkspaceWorkflowStage,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class SearchRunSubmission:
    """创建检索运行后返回给 API 的工作区与运行记录。"""

    collection: ResearchCollection
    research_plan: ResearchPlan
    search_run: SearchRun


class SearchRunService:
    """维护检索运行的长期状态，不执行外部 Provider 请求。"""

    def __init__(
        self,
        session: AsyncSession,
        queue: SearchRunJobQueue | None = None,
        *,
        settings: WorkflowSettings | None = None,
    ) -> None:
        self._session = session
        self._queue = queue
        self._settings = settings

    async def start_search(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
    ) -> SearchRunSubmission:
        """从已确认计划创建唯一活动检索，并投递异步 Worker。"""
        collection = await self._owned_collection_for_update(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
        )
        plan = await self._confirmed_plan_for_update(collection_id=collection_id)
        self._ensure_retrieval_stage(collection)
        await self._ensure_no_active_run(plan.id)
        await self._assert_submission_quota(owner_user_id)

        search_run = self._new_run(collection=collection, plan=plan, attempt_no=1)
        collection.workflow_stage = WorkspaceWorkflowStage.RETRIEVING.value
        self._session.add(search_run)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise SearchRunError(
                SearchRunErrorCode.ACTIVE_RUN_EXISTS,
                "当前研究计划已有排队或运行中的检索任务。",
            ) from exc

        await self._session.refresh(search_run)
        await self._enqueue_or_mark_failed(collection=collection, search_run=search_run)
        return SearchRunSubmission(
            collection=collection,
            research_plan=plan,
            search_run=search_run,
        )

    async def retry_search(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        previous_run_id: UUID,
    ) -> SearchRunSubmission:
        """为失败或过期运行创建新尝试，不覆盖历史运行记录。"""
        collection = await self._owned_collection_for_update(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
        )
        previous_run = await self._owned_run_for_update(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=previous_run_id,
        )
        retryable_statuses = {
            SearchRunStatus.PARTIAL_FAILED.value,
            SearchRunStatus.FAILED.value,
            SearchRunStatus.EXPIRED.value,
        }
        if previous_run.status not in retryable_statuses:
            raise SearchRunError(
                SearchRunErrorCode.RUN_NOT_RETRYABLE,
                "当前检索运行尚未失败或过期，不能重复执行。",
            )

        plan = await self._confirmed_plan_for_update(collection_id=collection_id)
        self._ensure_retrieval_stage(collection)
        await self._ensure_no_active_run(plan.id)
        await self._assert_submission_quota(owner_user_id)
        search_run = self._new_run(
            collection=collection,
            plan=plan,
            attempt_no=previous_run.attempt_no + 1,
        )
        collection.workflow_stage = WorkspaceWorkflowStage.RETRIEVING.value
        self._session.add(search_run)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise SearchRunError(
                SearchRunErrorCode.ACTIVE_RUN_EXISTS,
                "当前研究计划已有排队或运行中的检索任务。",
            ) from exc

        await self._session.refresh(search_run)
        await self._enqueue_or_mark_failed(collection=collection, search_run=search_run)
        return SearchRunSubmission(
            collection=collection,
            research_plan=plan,
            search_run=search_run,
        )

    async def get_current_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
    ) -> SearchRun:
        """读取当前用户工作区最近一次检索运行。"""
        statement = (
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
        run = await self._session.scalar(statement)
        if run is None:
            raise SearchRunError(SearchRunErrorCode.RUN_NOT_FOUND, "检索运行不存在。")
        return run

    async def get_owned_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> SearchRun:
        """读取指定运行并通过工作区所有权完成隔离。"""
        return await self._owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            lock=False,
        )

    async def claim_run(self, search_run_id: UUID) -> SearchRun | None:
        """Worker 领取排队任务；重复投递只会有一个 Worker 成功领取。"""
        statement = (
            select(SearchRun, ResearchCollection)
            .join(ResearchCollection, ResearchCollection.id == SearchRun.collection_id)
            .where(
                SearchRun.id == search_run_id,
                SearchRun.status == SearchRunStatus.QUEUED.value,
            )
            .with_for_update(of=(SearchRun, ResearchCollection))
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        search_run, collection = row._tuple()
        if collection.status != "active":
            await self._mark_run_failed_locked(
                search_run=search_run,
                collection=collection,
                error_code=SearchRunErrorCode.COLLECTION_NOT_ACTIVE.value,
                error_message="研究工作区已归档，不能开始文献检索。",
            )
            return None

        search_run.status = SearchRunStatus.RUNNING.value
        search_run.stage = SearchRunStage.PROVIDER_SEARCH.value
        search_run.started_at = datetime.now(UTC)
        search_run.error_code = None
        search_run.error_message = None
        collection.workflow_stage = WorkspaceWorkflowStage.RETRIEVING.value
        await self._session.commit()
        await self._session.refresh(search_run)
        return search_run

    async def complete_run(
        self,
        *,
        search_run_id: UUID,
        status: SearchRunStatus,
        provider_summary: dict[str, Any],
        candidate_counts: dict[str, Any],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> SearchRun | None:
        """持久化 Worker 的终态摘要，并推进工作区到候选审核或失败阶段。"""
        row = await self._run_and_collection_for_update(search_run_id)
        if row is None:
            return None
        search_run, collection = row
        if search_run.status in {
            SearchRunStatus.COMPLETED.value,
            SearchRunStatus.PARTIAL_FAILED.value,
            SearchRunStatus.FAILED.value,
            SearchRunStatus.EXPIRED.value,
            SearchRunStatus.CANCELLED.value,
        }:
            return search_run

        search_run.status = status.value
        search_run.stage = SearchRunStage.COMPLETED.value
        search_run.provider_summary = provider_summary
        search_run.candidate_counts = candidate_counts
        search_run.error_code = error_code
        search_run.error_message = error_message
        search_run.finished_at = datetime.now(UTC)
        collection.workflow_stage = (
            WorkspaceWorkflowStage.SCREENING.value
            if status in {SearchRunStatus.COMPLETED, SearchRunStatus.PARTIAL_FAILED}
            else WorkspaceWorkflowStage.FAILED.value
        )
        await self._session.commit()
        await self._session.refresh(search_run)
        return search_run

    async def update_progress(
        self,
        *,
        search_run_id: UUID,
        stage: SearchRunStage,
        provider_summary: dict[str, Any],
        candidate_counts: dict[str, Any],
    ) -> SearchRun | None:
        """持久化当前可恢复阶段和统计，供刷新页面时读取。"""
        row = await self._run_and_collection_for_update(search_run_id)
        if row is None:
            return None
        search_run, _collection = row
        if search_run.status != SearchRunStatus.RUNNING.value:
            return search_run
        search_run.stage = stage.value
        search_run.provider_summary = provider_summary
        search_run.candidate_counts = candidate_counts
        await self._session.commit()
        await self._session.refresh(search_run)
        return search_run

    async def reopen_relevance_run(
        self,
        *,
        search_run_id: UUID,
        candidate_counts: dict[str, Any],
    ) -> SearchRun | None:
        """在不重新请求 Provider 的前提下重新打开当前候选集合的相关性阶段。"""
        row = await self._run_and_collection_for_update(search_run_id)
        if row is None:
            return None
        search_run, collection = row
        if (
            search_run.status == SearchRunStatus.RUNNING.value
            and search_run.stage == SearchRunStage.RELEVANCE_ASSESSMENT.value
        ):
            return search_run
        if search_run.status not in {
            SearchRunStatus.COMPLETED.value,
            SearchRunStatus.PARTIAL_FAILED.value,
            SearchRunStatus.CANCELLED.value,
        }:
            return None
        search_run.status = SearchRunStatus.RUNNING.value
        search_run.stage = SearchRunStage.RELEVANCE_ASSESSMENT.value
        search_run.candidate_counts = candidate_counts
        search_run.error_code = None
        search_run.error_message = None
        search_run.finished_at = None
        collection.workflow_stage = WorkspaceWorkflowStage.SCREENING.value
        await self._session.commit()
        await self._session.refresh(search_run)
        return search_run

    async def cancel_relevance_run(
        self,
        *,
        search_run_id: UUID,
        candidate_counts: dict[str, Any],
    ) -> SearchRun | None:
        """显式取消当前语义分析，但保留已经检索到的候选供整批重试。"""
        row = await self._run_and_collection_for_update(search_run_id)
        if row is None:
            return None
        search_run, collection = row
        if (
            search_run.status != SearchRunStatus.RUNNING.value
            or search_run.stage != SearchRunStage.RELEVANCE_ASSESSMENT.value
        ):
            return None
        search_run.status = SearchRunStatus.CANCELLED.value
        search_run.stage = SearchRunStage.COMPLETED.value
        search_run.candidate_counts = candidate_counts
        search_run.error_code = "candidate_relevance_cancelled"
        search_run.error_message = "候选相关性分析已取消，可基于当前候选集合重新分析。"
        search_run.finished_at = datetime.now(UTC)
        collection.workflow_stage = WorkspaceWorkflowStage.SCREENING.value
        await self._session.commit()
        await self._session.refresh(search_run)
        return search_run

    async def expire_run(self, search_run_id: UUID) -> SearchRun | None:
        """当 Redis 候选会话不存在时，将终态运行标为过期并保留数据库审计。"""
        row = await self._run_and_collection_for_update(search_run_id)
        if row is None:
            return None
        search_run, _collection = row
        if search_run.status in {
            SearchRunStatus.COMPLETED.value,
            SearchRunStatus.PARTIAL_FAILED.value,
        }:
            search_run.status = SearchRunStatus.EXPIRED.value
            search_run.finished_at = search_run.finished_at or datetime.now(UTC)
            await self._session.commit()
            await self._session.refresh(search_run)
        return search_run

    async def _enqueue_or_mark_failed(
        self,
        *,
        collection: ResearchCollection,
        search_run: SearchRun,
    ) -> None:
        """检索运行落库后再投递；队列失败会明确写入终态。"""
        if self._queue is None:
            raise RuntimeError("创建检索运行时必须提供任务队列")
        try:
            search_run.arq_job_id = await self._queue.enqueue_search(search_run.id)
            await self._session.commit()
            await self._session.refresh(search_run)
        except SearchRunQueueError as exc:
            message = "文献检索任务无法投递，请稍后重试。"
            search_run.status = SearchRunStatus.FAILED.value
            search_run.stage = SearchRunStage.COMPLETED.value
            search_run.error_code = SearchRunErrorCode.QUEUE_UNAVAILABLE.value
            search_run.error_message = message
            search_run.finished_at = datetime.now(UTC)
            collection.workflow_stage = WorkspaceWorkflowStage.FAILED.value
            await self._session.commit()
            await self._session.refresh(search_run)
            raise SearchRunError(SearchRunErrorCode.QUEUE_UNAVAILABLE, message) from exc

    async def _assert_submission_quota(self, owner_user_id: UUID) -> None:
        """按 UTC 自然日限制用户与全局实际提交的检索运行数。"""
        settings = self._settings or get_workflow_settings()
        now = datetime.now(UTC)
        period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        user_run_count = int(
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
        if user_run_count >= settings.workflow_user_daily_search_run_limit:
            raise SearchRunError(
                SearchRunErrorCode.USER_QUOTA_EXCEEDED,
                "今日文献检索额度已用尽，请明天继续或联系管理员调整额度。",
            )
        global_run_count = int(
            await self._session.scalar(
                select(func.count(SearchRun.id)).where(SearchRun.created_at >= period_start)
            )
            or 0
        )
        if global_run_count >= settings.workflow_global_daily_search_run_limit:
            raise SearchRunError(
                SearchRunErrorCode.GLOBAL_BUDGET_EXHAUSTED,
                "今日全局文献检索预算已用尽，请稍后再试。",
            )

    async def _owned_collection_for_update(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
    ) -> ResearchCollection:
        """读取并锁定工作区，越权资源统一返回不存在。"""
        statement = (
            select(ResearchCollection)
            .where(
                ResearchCollection.id == collection_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status.in_(("active", "archived")),
            )
            .with_for_update()
        )
        collection = await self._session.scalar(statement)
        if collection is None:
            raise SearchRunError(SearchRunErrorCode.COLLECTION_NOT_FOUND, "研究工作区不存在。")
        if collection.status != "active":
            raise SearchRunError(
                SearchRunErrorCode.COLLECTION_NOT_ACTIVE,
                "研究工作区已归档，不能开始新的文献检索。",
            )
        return collection

    async def _confirmed_plan_for_update(self, *, collection_id: UUID) -> ResearchPlan:
        """锁定工作区最新已确认计划，防止检索使用旧版本查询。"""
        statement = (
            select(ResearchPlan)
            .where(
                ResearchPlan.collection_id == collection_id,
                ResearchPlan.status == ResearchPlanStatus.CONFIRMED.value,
            )
            .order_by(ResearchPlan.revision.desc())
            .limit(1)
            .with_for_update()
        )
        plan = await self._session.scalar(statement)
        if plan is None:
            raise SearchRunError(
                SearchRunErrorCode.PLAN_NOT_CONFIRMED,
                "请先确认研究方向、时间范围和语言范围。",
            )
        return plan

    async def _ensure_no_active_run(self, research_plan_id: UUID) -> None:
        """在数据库唯一索引之外提前返回可读的重复运行错误。"""
        statement = select(SearchRun).where(
            SearchRun.research_plan_id == research_plan_id,
            SearchRun.status.in_((SearchRunStatus.QUEUED.value, SearchRunStatus.RUNNING.value)),
        )
        if await self._session.scalar(statement) is not None:
            raise SearchRunError(
                SearchRunErrorCode.ACTIVE_RUN_EXISTS,
                "当前研究计划已有排队或运行中的检索任务。",
            )

    @staticmethod
    def _new_run(
        *,
        collection: ResearchCollection,
        plan: ResearchPlan,
        attempt_no: int,
    ) -> SearchRun:
        """构造新的检索运行头，候选详情不进入 PostgreSQL。"""
        run_id = uuid4()
        return SearchRun(
            id=run_id,
            collection_id=collection.id,
            research_plan_id=plan.id,
            redis_session_key=build_search_session_key(run_id),
            status=SearchRunStatus.QUEUED.value,
            stage=SearchRunStage.DISPATCH.value,
            attempt_no=attempt_no,
            provider_summary={},
            candidate_counts={},
        )

    def _ensure_retrieval_stage(self, collection: ResearchCollection) -> None:
        """限制检索只能从计划确认、失败恢复或候选重试阶段开始。"""
        allowed_stages = {
            WorkspaceWorkflowStage.PLAN_REVIEW.value,
            WorkspaceWorkflowStage.RETRIEVING.value,
            WorkspaceWorkflowStage.SCREENING.value,
            WorkspaceWorkflowStage.FAILED.value,
        }
        if collection.workflow_stage not in allowed_stages:
            raise SearchRunError(
                SearchRunErrorCode.PLAN_DATA_INVALID,
                "当前工作区不处于可开始文献检索的阶段。",
            )

    async def _owned_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        lock: bool,
    ) -> SearchRun:
        """按用户、工作区和运行标识读取运行，形成三重归属边界。"""
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
        if lock:
            statement = statement.with_for_update()
        run = await self._session.scalar(statement)
        if run is None:
            raise SearchRunError(SearchRunErrorCode.RUN_NOT_FOUND, "检索运行不存在。")
        return run

    async def _owned_run_for_update(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> SearchRun:
        """锁定指定运行，用于创建不可覆盖的重试版本。"""
        return await self._owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            lock=True,
        )

    async def _run_and_collection_for_update(
        self,
        search_run_id: UUID,
    ) -> tuple[SearchRun, ResearchCollection] | None:
        """Worker 按运行标识锁定运行和工作区，过期队列消息安全忽略。"""
        statement = (
            select(SearchRun, ResearchCollection)
            .join(ResearchCollection, ResearchCollection.id == SearchRun.collection_id)
            .where(SearchRun.id == search_run_id)
            .with_for_update(of=(SearchRun, ResearchCollection))
        )
        row = (await self._session.execute(statement)).one_or_none()
        return row._tuple() if row is not None else None

    async def _mark_run_failed_locked(
        self,
        *,
        search_run: SearchRun,
        collection: ResearchCollection,
        error_code: str,
        error_message: str,
    ) -> None:
        """在领取阶段发现不可执行条件时写入失败状态。"""
        search_run.status = SearchRunStatus.FAILED.value
        search_run.stage = SearchRunStage.COMPLETED.value
        search_run.error_code = error_code
        search_run.error_message = error_message
        search_run.finished_at = datetime.now(UTC)
        collection.workflow_stage = WorkspaceWorkflowStage.FAILED.value
        await self._session.commit()
