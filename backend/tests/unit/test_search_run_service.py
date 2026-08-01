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


@pytest.mark.asyncio
async def test_start_search_requires_confirmed_plan_and_queues_once() -> None:
    """只有确认计划才能创建运行，成功后使用服务端生成的运行 UUID 投递。"""
    collection = _collection()
    plan = _plan()
    session = FakeSession([collection, plan, None])
    queue = FakeQueue()

    submission = await SearchRunService(cast(AsyncSession, session), queue).start_search(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
    )

    assert submission.search_run.status == SearchRunStatus.QUEUED.value
    assert submission.search_run.research_plan_id == _PLAN_ID
    assert collection.workflow_stage == WorkspaceWorkflowStage.RETRIEVING.value
    assert queue.enqueued_run_ids == [submission.search_run.id]

    not_confirmed = FakeSession([collection, None])
    with pytest.raises(SearchRunError) as error:
        await SearchRunService(cast(AsyncSession, not_confirmed), FakeQueue()).start_search(
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
    session = FakeSession([_collection(stage="screening"), previous, _plan(), None])
    queue = FakeQueue()

    submission = await SearchRunService(cast(AsyncSession, session), queue).retry_search(
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
    session = FakeSession([collection, _plan(), None])

    with pytest.raises(SearchRunError) as error:
        await SearchRunService(cast(AsyncSession, session), FakeQueue(fail=True)).start_search(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
        )

    run = session.added[0]
    assert isinstance(run, SearchRun)
    assert run.status == SearchRunStatus.FAILED.value
    assert collection.workflow_stage == WorkspaceWorkflowStage.FAILED.value
    assert error.value.code is SearchRunErrorCode.QUEUE_UNAVAILABLE
