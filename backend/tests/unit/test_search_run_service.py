"""检索运行服务的前置条件、重试和重复提交测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID

import pytest
from app.db.models.collection import ResearchCollection
from app.db.models.workflow import ResearchPlan, SearchRun
from app.modules.workflow.contracts import SearchRunError, SearchRunErrorCode
from app.modules.workflow.job_queue import SearchRunQueueError
from app.modules.workflow.search_run_service import SearchRunService
from app.modules.workflow.settings import WorkflowSettings
from app.modules.workflow.state import ResearchPlanStatus, SearchRunStatus, WorkspaceWorkflowStage
from sqlalchemy.ext.asyncio import AsyncSession

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


class FakeSession:
    """检索运行服务所需的最小异步会话替身。"""

    def __init__(self, scalar_values: list[object | None]) -> None:
        self._scalar_values = iter(scalar_values)
        self.added: list[object] = []
        self.commit_count = 0
        self.rollback_count = 0

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self

    async def scalar(self, _statement: object) -> object | None:
        return next(self._scalar_values)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def refresh(self, _instance: object) -> None:
        return None


def _collection(*, stage: str = "plan_review") -> ResearchCollection:
    """构造已通过认证归属检查的活动工作区。"""
    return ResearchCollection(
        id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        name="Search test workspace",
        status="active",
        workflow_stage=stage,
    )


def _plan(*, status: str = ResearchPlanStatus.CONFIRMED.value) -> ResearchPlan:
    """构造含有一个方向查询的研究计划。"""
    return ResearchPlan(
        id=_PLAN_ID,
        collection_id=_COLLECTION_ID,
        revision=1,
        raw_request="城市绿地如何影响心理健康？",
        status=status,
        direction_options=[{"id": "green-space", "title": "绿地与心理健康"}],
        scope={"confirmed": {"start_year": 2020, "end_year": 2024, "languages": ["zh"]}},
        query_plan={
            "selected_direction_id": "green-space",
            "queries": [{"provider": "openalex", "query": "green space mental health"}],
        },
        model_snapshot={},
    )


def _workflow_settings(*, user_limit: int = 20, global_limit: int = 500) -> WorkflowSettings:
    """仅构造本服务读取的配额字段，避免单元测试依赖本地模型凭据。"""
    return WorkflowSettings.model_construct(
        workflow_user_daily_search_run_limit=user_limit,
        workflow_global_daily_search_run_limit=global_limit,
    )


@pytest.mark.asyncio
async def test_start_search_requires_confirmed_plan_and_queues_once() -> None:
    """只有确认计划才能创建运行，成功后使用服务端生成的运行 UUID 投递。"""
    collection = _collection()
    plan = _plan()
    session = FakeSession([collection, plan, None, 0, 0])
    queue = FakeQueue()

    submission = await SearchRunService(
        cast(AsyncSession, session), queue, settings=_workflow_settings()
    ).start_search(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
    )

    assert submission.search_run.status == SearchRunStatus.QUEUED.value
    assert submission.search_run.research_plan_id == _PLAN_ID
    assert collection.workflow_stage == WorkspaceWorkflowStage.RETRIEVING.value
    assert queue.enqueued_run_ids == [submission.search_run.id]

    not_confirmed = FakeSession([collection, None])
    with pytest.raises(SearchRunError) as error:
        await SearchRunService(
            cast(AsyncSession, not_confirmed), FakeQueue(), settings=_workflow_settings()
        ).start_search(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
        )
    assert error.value.code is SearchRunErrorCode.PLAN_NOT_CONFIRMED


@pytest.mark.asyncio
async def test_start_search_rejects_an_existing_active_run() -> None:
    """重复点击不会创建第二条 queued/running 运行。"""
    active_run = SearchRun(
        id=_RUN_ID,
        collection_id=_COLLECTION_ID,
        research_plan_id=_PLAN_ID,
        status=SearchRunStatus.RUNNING.value,
        stage="provider_search",
        attempt_no=1,
        provider_summary={},
        candidate_counts={},
    )
    session = FakeSession([_collection(), _plan(), active_run])

    with pytest.raises(SearchRunError) as error:
        await SearchRunService(cast(AsyncSession, session), FakeQueue()).start_search(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
        )

    assert error.value.code is SearchRunErrorCode.ACTIVE_RUN_EXISTS
    assert session.added == []


@pytest.mark.asyncio
async def test_retry_creates_a_new_attempt_without_overwriting_history() -> None:
    """失败运行重试时递增 attempt_no，并保留旧运行对象。"""
    previous = SearchRun(
        id=_RUN_ID,
        collection_id=_COLLECTION_ID,
        research_plan_id=_PLAN_ID,
        status=SearchRunStatus.PARTIAL_FAILED.value,
        stage="completed",
        attempt_no=2,
        provider_summary={},
        candidate_counts={},
    )
    session = FakeSession([_collection(stage="screening"), previous, _plan(), None, 0, 0])
    queue = FakeQueue()

    submission = await SearchRunService(
        cast(AsyncSession, session), queue, settings=_workflow_settings()
    ).retry_search(
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
    """Redis 不可用时保留失败运行，不返回伪成功。"""
    collection = _collection()
    session = FakeSession([collection, _plan(), None, 0, 0])

    with pytest.raises(SearchRunError) as error:
        await SearchRunService(
            cast(AsyncSession, session), FakeQueue(fail=True), settings=_workflow_settings()
        ).start_search(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
        )

    run = session.added[0]
    assert isinstance(run, SearchRun)
    assert run.status == SearchRunStatus.FAILED.value
    assert collection.workflow_stage == WorkspaceWorkflowStage.FAILED.value
    assert error.value.code is SearchRunErrorCode.QUEUE_UNAVAILABLE


@pytest.mark.asyncio
async def test_start_search_rejects_user_and_global_daily_submission_limits() -> None:
    """检索运行在落库前检查用户与全局 UTC 自然日预算。"""
    user_limited = FakeSession([_collection(), _plan(), None, 1])
    with pytest.raises(SearchRunError) as user_error:
        await SearchRunService(
            cast(AsyncSession, user_limited),
            FakeQueue(),
            settings=_workflow_settings(user_limit=1),
        ).start_search(owner_user_id=_OWNER_ID, collection_id=_COLLECTION_ID)

    assert user_error.value.code is SearchRunErrorCode.USER_QUOTA_EXCEEDED
    assert user_limited.added == []

    global_limited = FakeSession([_collection(), _plan(), None, 0, 1])
    with pytest.raises(SearchRunError) as global_error:
        await SearchRunService(
            cast(AsyncSession, global_limited),
            FakeQueue(),
            settings=_workflow_settings(global_limit=1),
        ).start_search(owner_user_id=_OWNER_ID, collection_id=_COLLECTION_ID)

    assert global_error.value.code is SearchRunErrorCode.GLOBAL_BUDGET_EXHAUSTED
    assert global_limited.added == []


@pytest.mark.asyncio
async def test_retry_search_consumes_the_same_daily_submission_budget() -> None:
    """失败检索的重试同样在创建新运行前受配额保护。"""
    previous = SearchRun(
        id=_RUN_ID,
        collection_id=_COLLECTION_ID,
        research_plan_id=_PLAN_ID,
        status=SearchRunStatus.FAILED.value,
        stage="completed",
        attempt_no=1,
        provider_summary={},
        candidate_counts={},
    )
    session = FakeSession([_collection(stage="failed"), previous, _plan(), None, 1])

    with pytest.raises(SearchRunError) as error:
        await SearchRunService(
            cast(AsyncSession, session),
            FakeQueue(),
            settings=_workflow_settings(user_limit=1),
        ).retry_search(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            previous_run_id=_RUN_ID,
        )

    assert error.value.code is SearchRunErrorCode.USER_QUOTA_EXCEEDED
    assert session.added == []
