"""检索运行服务的前置条件、重试和重复提交测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.core.workflow_settings import WorkflowSettings
from app.modules.research.plan_models import ResearchPlanRecord
from app.modules.research.state import ResearchPlanStatus, WorkspaceWorkflowStage
from app.modules.search.api_contracts import SearchRunError, SearchRunErrorCode
from app.modules.search.queue import SearchRunQueueError
from app.modules.search.run_models import (
    DailySearchRunCounts,
    SearchRunContext,
    SearchRunRecord,
    SearchWorkspace,
)
from app.modules.search.run_repository import CreateSearchRun
from app.modules.search.run_service import SearchRunService
from app.modules.search.state import SearchRunStage, SearchRunStatus

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000401")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000402")
_PLAN_ID = UUID("00000000-0000-0000-0000-000000000403")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000404")


class FakeQueue:
    """记录检索任务投递调用的队列替身。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.enqueued_run_ids: list[UUID] = []

    async def enqueue_search(self, search_run_id: UUID) -> str:
        if self.fail:
            raise SearchRunQueueError("test queue unavailable")
        self.enqueued_run_ids.append(search_run_id)
        return f"job-{search_run_id}"


class FakeSearchRunRepository:
    """In-memory search-run port replacement for command tests."""

    def __init__(
        self,
        *,
        workspace: SearchWorkspace | None = None,
        plan: ResearchPlanRecord | None = None,
        run: SearchRunRecord | None = None,
        has_active_run: bool = False,
        counts: DailySearchRunCounts | None = None,
    ) -> None:
        self.workspace = workspace
        self.plan = plan
        self.run = run
        self._has_active_run = has_active_run
        self.counts = counts or DailySearchRunCounts(user=0, global_=0)
        self.created_commands: list[CreateSearchRun] = []
        self.saved_contexts: list[SearchRunContext] = []

    async def get_owned_workspace_for_update(
        self, *, owner_user_id: UUID, collection_id: UUID
    ) -> SearchWorkspace | None:
        if (
            self.workspace is None
            or self.workspace.owner_user_id != owner_user_id
            or self.workspace.id != collection_id
        ):
            return None
        return self.workspace

    async def get_confirmed_plan_for_update(
        self, *, collection_id: UUID
    ) -> ResearchPlanRecord | None:
        if self.plan is None or self.plan.collection_id != collection_id:
            return None
        return self.plan

    async def get_current_run(
        self, *, owner_user_id: UUID, collection_id: UUID
    ) -> SearchRunRecord | None:
        if self.workspace is None or self.workspace.owner_user_id != owner_user_id:
            return None
        if self.run is None or self.run.collection_id != collection_id:
            return None
        return self.run

    async def get_owned_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        for_update: bool = False,
    ) -> SearchRunRecord | None:
        del for_update
        run = await self.get_current_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
        )
        return run if run is not None and run.id == search_run_id else None

    async def has_active_run(self, research_plan_id: UUID) -> bool:
        assert research_plan_id == _PLAN_ID
        return self._has_active_run

    async def count_since(
        self, *, owner_user_id: UUID, period_start: datetime
    ) -> DailySearchRunCounts:
        assert owner_user_id == _OWNER_ID
        assert period_start.tzinfo is not None
        return self.counts

    async def create_run(
        self, *, workspace: SearchWorkspace, command: CreateSearchRun
    ) -> SearchRunContext:
        self.created_commands.append(command)
        now = datetime.now(UTC)
        self.workspace = workspace
        self.run = SearchRunRecord(
            id=command.run_id,
            collection_id=command.collection_id,
            research_plan_id=command.research_plan_id,
            arq_job_id=None,
            redis_session_key=command.redis_session_key,
            status=SearchRunStatus.QUEUED.value,
            stage=SearchRunStage.DISPATCH.value,
            attempt_no=command.attempt_no,
            provider_summary={},
            candidate_counts={},
            error_code=None,
            error_message=None,
            started_at=None,
            finished_at=None,
            created_at=now,
            updated_at=now,
        )
        return SearchRunContext(workspace, self.run)

    async def get_run_context_for_update(self, search_run_id: UUID) -> SearchRunContext | None:
        if self.workspace is None or self.run is None or self.run.id != search_run_id:
            return None
        return SearchRunContext(self.workspace, self.run)

    async def get_relevance_run_for_update(self, search_run_id: UUID) -> SearchRunRecord | None:
        return self.run if self.run is not None and self.run.id == search_run_id else None

    async def get_plan(self, research_plan_id: UUID) -> ResearchPlanRecord | None:
        return self.plan if self.plan is not None and self.plan.id == research_plan_id else None

    async def save(self, context: SearchRunContext) -> SearchRunContext:
        self.workspace = context.workspace
        self.run = context.run
        self.saved_contexts.append(context)
        return context


def _collection(*, stage: str = "plan_review") -> SearchWorkspace:
    return SearchWorkspace(
        id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        status="active",
        workflow_stage=stage,
    )


def _plan(*, status: str = ResearchPlanStatus.CONFIRMED.value) -> ResearchPlanRecord:
    now = datetime.now(UTC)
    return ResearchPlanRecord(
        id=_PLAN_ID,
        collection_id=_COLLECTION_ID,
        revision=1,
        raw_request="城市绿地如何影响心理健康？",
        status=status,
        direction_options=[{"id": "green-space", "title": "绿地与心理健康"}],
        selected_direction_id="green-space",
        scope={"confirmed": {"start_year": 2020, "end_year": 2024, "languages": ["zh"]}},
        query_plan={
            "selected_direction_id": "green-space",
            "queries": [{"provider": "openalex", "query": "green space mental health"}],
        },
        model_snapshot={},
        arq_job_id=None,
        error_code=None,
        error_message=None,
        confirmed_at=now,
        created_at=now,
        updated_at=now,
    )


def _run(*, status: str, attempt_no: int) -> SearchRunRecord:
    now = datetime.now(UTC)
    return SearchRunRecord(
        id=_RUN_ID,
        collection_id=_COLLECTION_ID,
        research_plan_id=_PLAN_ID,
        arq_job_id=None,
        redis_session_key=f"search:session:{_RUN_ID}",
        status=status,
        stage=SearchRunStage.COMPLETED.value,
        attempt_no=attempt_no,
        provider_summary={},
        candidate_counts={},
        error_code=None,
        error_message=None,
        started_at=now,
        finished_at=now,
        created_at=now,
        updated_at=now,
    )


def _workflow_settings(*, user_limit: int = 20, global_limit: int = 500) -> WorkflowSettings:
    return WorkflowSettings.model_construct(
        workflow_user_daily_search_run_limit=user_limit,
        workflow_global_daily_search_run_limit=global_limit,
    )


@pytest.mark.asyncio
async def test_start_search_requires_confirmed_plan_and_queues_once() -> None:
    collection = _collection()
    plans = FakeSearchRunRepository(workspace=collection, plan=_plan())
    queue = FakeQueue()

    submission = await SearchRunService(plans, queue, settings=_workflow_settings()).start_search(
        owner_user_id=_OWNER_ID, collection_id=_COLLECTION_ID
    )

    assert submission.search_run.status == SearchRunStatus.QUEUED.value
    assert submission.search_run.research_plan_id == _PLAN_ID
    assert submission.collection.workflow_stage == WorkspaceWorkflowStage.RETRIEVING.value
    assert queue.enqueued_run_ids == [submission.search_run.id]

    not_confirmed = FakeSearchRunRepository(workspace=collection)
    with pytest.raises(SearchRunError) as error:
        await SearchRunService(
            not_confirmed, FakeQueue(), settings=_workflow_settings()
        ).start_search(owner_user_id=_OWNER_ID, collection_id=_COLLECTION_ID)
    assert error.value.code is SearchRunErrorCode.PLAN_NOT_CONFIRMED


@pytest.mark.asyncio
async def test_start_search_rejects_an_existing_active_run() -> None:
    runs = FakeSearchRunRepository(
        workspace=_collection(),
        plan=_plan(),
        has_active_run=True,
    )
    with pytest.raises(SearchRunError) as error:
        await SearchRunService(runs, FakeQueue()).start_search(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
        )

    assert error.value.code is SearchRunErrorCode.ACTIVE_RUN_EXISTS
    assert runs.created_commands == []


@pytest.mark.asyncio
async def test_retry_creates_a_new_attempt_without_overwriting_history() -> None:
    previous = _run(status=SearchRunStatus.PARTIAL_FAILED.value, attempt_no=2)
    runs = FakeSearchRunRepository(
        workspace=_collection(stage="screening"),
        plan=_plan(),
        run=previous,
    )
    queue = FakeQueue()

    submission = await SearchRunService(runs, queue, settings=_workflow_settings()).retry_search(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        previous_run_id=_RUN_ID,
    )

    assert submission.search_run.attempt_no == 3
    assert submission.search_run.id != _RUN_ID
    assert previous.status == SearchRunStatus.PARTIAL_FAILED.value
    assert submission.collection.workflow_stage == WorkspaceWorkflowStage.RETRIEVING.value


@pytest.mark.asyncio
async def test_queue_failure_marks_run_and_workspace_failed() -> None:
    runs = FakeSearchRunRepository(workspace=_collection(), plan=_plan())

    with pytest.raises(SearchRunError) as error:
        await SearchRunService(
            runs, FakeQueue(fail=True), settings=_workflow_settings()
        ).start_search(owner_user_id=_OWNER_ID, collection_id=_COLLECTION_ID)

    assert runs.run is not None
    assert runs.run.status == SearchRunStatus.FAILED.value
    assert runs.workspace is not None
    assert runs.workspace.workflow_stage == WorkspaceWorkflowStage.FAILED.value
    assert error.value.code is SearchRunErrorCode.QUEUE_UNAVAILABLE


@pytest.mark.asyncio
async def test_start_search_rejects_user_and_global_daily_submission_limits() -> None:
    user_limited = FakeSearchRunRepository(
        workspace=_collection(),
        plan=_plan(),
        counts=DailySearchRunCounts(user=1, global_=1),
    )
    with pytest.raises(SearchRunError) as user_error:
        await SearchRunService(
            user_limited,
            FakeQueue(),
            settings=_workflow_settings(user_limit=1),
        ).start_search(owner_user_id=_OWNER_ID, collection_id=_COLLECTION_ID)
    assert user_error.value.code is SearchRunErrorCode.USER_QUOTA_EXCEEDED
    assert user_limited.created_commands == []

    global_limited = FakeSearchRunRepository(
        workspace=_collection(),
        plan=_plan(),
        counts=DailySearchRunCounts(user=0, global_=1),
    )
    with pytest.raises(SearchRunError) as global_error:
        await SearchRunService(
            global_limited,
            FakeQueue(),
            settings=_workflow_settings(global_limit=1),
        ).start_search(owner_user_id=_OWNER_ID, collection_id=_COLLECTION_ID)
    assert global_error.value.code is SearchRunErrorCode.GLOBAL_BUDGET_EXHAUSTED
    assert global_limited.created_commands == []


@pytest.mark.asyncio
async def test_retry_search_consumes_the_same_daily_submission_budget() -> None:
    runs = FakeSearchRunRepository(
        workspace=_collection(stage="failed"),
        plan=_plan(),
        run=_run(status=SearchRunStatus.FAILED.value, attempt_no=1),
        counts=DailySearchRunCounts(user=1, global_=1),
    )

    with pytest.raises(SearchRunError) as error:
        await SearchRunService(
            runs,
            FakeQueue(),
            settings=_workflow_settings(user_limit=1),
        ).retry_search(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            previous_run_id=_RUN_ID,
        )

    assert error.value.code is SearchRunErrorCode.USER_QUOTA_EXCEEDED
    assert runs.created_commands == []
