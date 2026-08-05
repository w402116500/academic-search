"""arq 工作流与入库任务必须路由到不同队列的回归测试。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from app.infra.redis.job_queues import (
    ArqCandidateFulltextJobQueue,
    ArqIngestionJobQueue,
    ArqSearchRunJobQueue,
)
from app.infra.redis.queues import INGESTION_QUEUE_NAME, WORKFLOW_QUEUE_NAME


class FakeJob:
    """模拟 arq 返回的最小任务对象。"""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id


class FakeRedis:
    """记录投递参数，不连接真实 Redis。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> FakeJob:
        self.calls.append((function, args, kwargs))
        return FakeJob(kwargs["_job_id"])

    async def aclose(self, *, close_connection_pool: bool) -> None:
        assert close_connection_pool is True


@pytest.mark.asyncio
async def test_workflow_and_ingestion_queues_are_explicitly_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不同 Worker 不能消费默认队列中的彼此任务。"""
    redis = FakeRedis()

    async def create_pool(_settings: object) -> FakeRedis:
        return redis

    monkeypatch.setattr("app.infra.redis.job_queues.create_pool", create_pool)

    search_run_id = uuid4()
    candidate_id = uuid4()
    ingestion_run_id = uuid4()
    await ArqSearchRunJobQueue().enqueue_search(search_run_id)
    await ArqCandidateFulltextJobQueue().enqueue_fulltext(
        search_run_id=search_run_id,
        candidate_id=candidate_id,
        attempt_no=1,
    )
    await ArqIngestionJobQueue().enqueue_ingestion(ingestion_run_id)

    assert [call[2]["_queue_name"] for call in redis.calls] == [
        WORKFLOW_QUEUE_NAME,
        WORKFLOW_QUEUE_NAME,
        INGESTION_QUEUE_NAME,
    ]
