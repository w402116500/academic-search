"""多源文献检索运行的事务服务。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.workflow_settings import WorkflowSettings
from app.modules.research.plan_models import ResearchPlanRecord
from app.modules.research.state import WorkspaceWorkflowStage
from app.modules.search.api_contracts import SearchRunError, SearchRunErrorCode
from app.modules.search.queue import SearchRunJobQueue, SearchRunQueueError
from app.modules.search.run_models import SearchRunContext, SearchRunRecord, SearchWorkspace
from app.modules.search.run_repository import (
    ActiveSearchRunConflict,
    CreateSearchRun,
    SearchRunRepository,
)
from app.modules.search.session import build_search_session_key
from app.modules.search.state import SearchRunStage, SearchRunStatus


@dataclass(frozen=True, slots=True)
class SearchRunSubmission:
    """创建检索运行后返回给 API 的工作区、计划与运行记录。"""

    collection: SearchWorkspace
    research_plan: ResearchPlanRecord
    search_run: SearchRunRecord


class SearchRunService:
    """维护检索运行的长期状态，不执行外部 Provider 请求。"""

    def __init__(
        self,
        runs: SearchRunRepository,
        queue: SearchRunJobQueue | None = None,
        *,
        settings: WorkflowSettings | None = None,
    ) -> None:
        self._runs = runs
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
        context = await self._create_run(
            collection=collection,
            plan=plan,
            attempt_no=1,
        )
        context = await self._enqueue_or_mark_failed(context)
        return SearchRunSubmission(context.workspace, plan, context.run)

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
        previous_run = await self._owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=previous_run_id,
            for_update=True,
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
        context = await self._create_run(
            collection=collection,
            plan=plan,
            attempt_no=previous_run.attempt_no + 1,
        )
        context = await self._enqueue_or_mark_failed(context)
        return SearchRunSubmission(context.workspace, plan, context.run)

    async def get_current_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
    ) -> SearchRunRecord:
        """读取当前用户工作区最近一次检索运行。"""
        run = await self._runs.get_current_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
        )
        if run is None:
            raise SearchRunError(SearchRunErrorCode.RUN_NOT_FOUND, "检索运行不存在。")
        return run

    async def get_owned_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> SearchRunRecord:
        """读取指定运行并通过工作区所有权完成隔离。"""
        return await self._owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            for_update=False,
        )

    async def claim_run(self, search_run_id: UUID) -> SearchRunRecord | None:
        """Worker 领取排队任务；重复投递只会有一个 Worker 成功领取。"""
        context = await self._runs.get_run_context_for_update(search_run_id)
        if context is None or context.run.status != SearchRunStatus.QUEUED.value:
            return None
        if context.workspace.status != "active":
            await self._runs.save(
                self._failed_context(
                    context,
                    error_code=SearchRunErrorCode.COLLECTION_NOT_ACTIVE.value,
                    error_message="研究工作区已归档，不能开始文献检索。",
                )
            )
            return None

        claimed = replace(
            context,
            workspace=replace(
                context.workspace,
                workflow_stage=WorkspaceWorkflowStage.RETRIEVING.value,
            ),
            run=replace(
                context.run,
                status=SearchRunStatus.RUNNING.value,
                stage=SearchRunStage.PROVIDER_SEARCH.value,
                started_at=datetime.now(UTC),
                error_code=None,
                error_message=None,
            ),
        )
        return (await self._runs.save(claimed)).run

    async def complete_run(
        self,
        *,
        search_run_id: UUID,
        status: SearchRunStatus,
        provider_summary: dict[str, Any],
        candidate_counts: dict[str, Any],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> SearchRunRecord | None:
        """持久化 Worker 的终态摘要，并推进工作区到候选审核或失败阶段。"""
        context = await self._runs.get_run_context_for_update(search_run_id)
        if context is None:
            return None
        if context.run.status in {
            SearchRunStatus.COMPLETED.value,
            SearchRunStatus.PARTIAL_FAILED.value,
            SearchRunStatus.FAILED.value,
            SearchRunStatus.EXPIRED.value,
            SearchRunStatus.CANCELLED.value,
        }:
            return context.run

        completed = replace(
            context,
            workspace=replace(
                context.workspace,
                workflow_stage=(
                    WorkspaceWorkflowStage.SCREENING.value
                    if status in {SearchRunStatus.COMPLETED, SearchRunStatus.PARTIAL_FAILED}
                    else WorkspaceWorkflowStage.FAILED.value
                ),
            ),
            run=replace(
                context.run,
                status=status.value,
                stage=SearchRunStage.COMPLETED.value,
                provider_summary=provider_summary,
                candidate_counts=candidate_counts,
                error_code=error_code,
                error_message=error_message,
                finished_at=datetime.now(UTC),
            ),
        )
        return (await self._runs.save(completed)).run

    async def update_progress(
        self,
        *,
        search_run_id: UUID,
        stage: SearchRunStage,
        provider_summary: dict[str, Any],
        candidate_counts: dict[str, Any],
    ) -> SearchRunRecord | None:
        """持久化当前可恢复阶段和统计，供刷新页面时读取。"""
        context = await self._runs.get_run_context_for_update(search_run_id)
        if context is None:
            return None
        if context.run.status != SearchRunStatus.RUNNING.value:
            return context.run
        updated = replace(
            context,
            run=replace(
                context.run,
                stage=stage.value,
                provider_summary=provider_summary,
                candidate_counts=candidate_counts,
            ),
        )
        return (await self._runs.save(updated)).run

    async def expire_run(self, search_run_id: UUID) -> SearchRunRecord | None:
        """当检索运行确实不可恢复时，将终态运行标为过期并保留数据库审计。"""
        context = await self._runs.get_run_context_for_update(search_run_id)
        if context is None:
            return None
        if context.run.status not in {
            SearchRunStatus.COMPLETED.value,
            SearchRunStatus.PARTIAL_FAILED.value,
        }:
            return context.run
        expired = replace(
            context,
            run=replace(
                context.run,
                status=SearchRunStatus.EXPIRED.value,
                finished_at=context.run.finished_at or datetime.now(UTC),
            ),
        )
        return (await self._runs.save(expired)).run

    async def _create_run(
        self,
        *,
        collection: SearchWorkspace,
        plan: ResearchPlanRecord,
        attempt_no: int,
    ) -> SearchRunContext:
        run_id = uuid4()
        try:
            return await self._runs.create_run(
                workspace=replace(
                    collection,
                    workflow_stage=WorkspaceWorkflowStage.RETRIEVING.value,
                ),
                command=CreateSearchRun(
                    run_id=run_id,
                    collection_id=collection.id,
                    research_plan_id=plan.id,
                    redis_session_key=build_search_session_key(run_id),
                    attempt_no=attempt_no,
                ),
            )
        except ActiveSearchRunConflict as exc:
            raise SearchRunError(
                SearchRunErrorCode.ACTIVE_RUN_EXISTS,
                "当前研究计划已有排队或运行中的检索任务。",
            ) from exc

    async def _enqueue_or_mark_failed(self, context: SearchRunContext) -> SearchRunContext:
        """检索运行落库后再投递；队列失败会明确写入终态。"""
        if self._queue is None:
            raise RuntimeError("创建检索运行时必须提供任务队列")
        try:
            job_id = await self._queue.enqueue_search(context.run.id)
            return await self._runs.save(
                replace(context, run=replace(context.run, arq_job_id=job_id))
            )
        except SearchRunQueueError as exc:
            message = "文献检索任务无法投递，请稍后重试。"
            await self._runs.save(
                self._failed_context(
                    context,
                    error_code=SearchRunErrorCode.QUEUE_UNAVAILABLE.value,
                    error_message=message,
                )
            )
            raise SearchRunError(SearchRunErrorCode.QUEUE_UNAVAILABLE, message) from exc

    async def _assert_submission_quota(self, owner_user_id: UUID) -> None:
        """按 UTC 自然日限制用户与全局实际提交的检索运行数。"""
        settings = self._settings
        if settings is None:
            raise RuntimeError("创建检索运行时必须提供工作流配额配置")
        now = datetime.now(UTC)
        counts = await self._runs.count_since(
            owner_user_id=owner_user_id,
            period_start=now.replace(hour=0, minute=0, second=0, microsecond=0),
        )
        if counts.user >= settings.workflow_user_daily_search_run_limit:
            raise SearchRunError(
                SearchRunErrorCode.USER_QUOTA_EXCEEDED,
                "今日文献检索额度已用尽，请明天继续或联系管理员调整额度。",
            )
        if counts.global_ >= settings.workflow_global_daily_search_run_limit:
            raise SearchRunError(
                SearchRunErrorCode.GLOBAL_BUDGET_EXHAUSTED,
                "今日全局文献检索预算已用尽，请稍后再试。",
            )

    async def _owned_collection_for_update(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
    ) -> SearchWorkspace:
        collection = await self._runs.get_owned_workspace_for_update(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
        )
        if collection is None:
            raise SearchRunError(SearchRunErrorCode.COLLECTION_NOT_FOUND, "研究工作区不存在。")
        if collection.status != "active":
            raise SearchRunError(
                SearchRunErrorCode.COLLECTION_NOT_ACTIVE,
                "研究工作区已归档，不能开始新的文献检索。",
            )
        return collection

    async def _confirmed_plan_for_update(self, *, collection_id: UUID) -> ResearchPlanRecord:
        plan = await self._runs.get_confirmed_plan_for_update(collection_id=collection_id)
        if plan is None:
            raise SearchRunError(
                SearchRunErrorCode.PLAN_NOT_CONFIRMED,
                "请先确认研究方向、时间范围和语言范围。",
            )
        return plan

    async def _ensure_no_active_run(self, research_plan_id: UUID) -> None:
        if await self._runs.has_active_run(research_plan_id):
            raise SearchRunError(
                SearchRunErrorCode.ACTIVE_RUN_EXISTS,
                "当前研究计划已有排队或运行中的检索任务。",
            )

    @staticmethod
    def _ensure_retrieval_stage(collection: SearchWorkspace) -> None:
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
        for_update: bool,
    ) -> SearchRunRecord:
        run = await self._runs.get_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            for_update=for_update,
        )
        if run is None:
            raise SearchRunError(SearchRunErrorCode.RUN_NOT_FOUND, "检索运行不存在。")
        return run

    @staticmethod
    def _failed_context(
        context: SearchRunContext,
        *,
        error_code: str,
        error_message: str,
    ) -> SearchRunContext:
        return replace(
            context,
            workspace=replace(
                context.workspace,
                workflow_stage=WorkspaceWorkflowStage.FAILED.value,
            ),
            run=replace(
                context.run,
                status=SearchRunStatus.FAILED.value,
                stage=SearchRunStage.COMPLETED.value,
                error_code=error_code,
                error_message=error_message,
                finished_at=datetime.now(UTC),
            ),
        )
