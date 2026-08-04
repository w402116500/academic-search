from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.modules.research.job_queue import ArqResearchJobQueue
from app.workers.queues import RESEARCH_QUEUE_NAME


class _FakeRedis:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    async def enqueue_job(self, _function: str, _run_id: str, **kwargs: object) -> SimpleNamespace:
        job_id = kwargs["_job_id"]
        assert isinstance(job_id, str)
        assert kwargs["_queue_name"] == RESEARCH_QUEUE_NAME
        self.job_ids.append(job_id)
        return SimpleNamespace(job_id=job_id)

    async def aclose(self, *, close_connection_pool: bool) -> None:
        assert close_connection_pool is True


@pytest.mark.asyncio
async def test_research_retry_uses_new_arq_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧任务结果仍在 Redis 时，业务重试不能复用首次的幂等 job ID。"""

    redis = _FakeRedis()

    async def _create_pool(_: object) -> _FakeRedis:
        return redis

    monkeypatch.setattr("app.modules.research.job_queue.create_pool", _create_pool)
    queue = ArqResearchJobQueue()
    research_run_id = uuid4()

    initial_job_id = await queue.enqueue_research(research_run_id)
    retry_job_id = await queue.enqueue_research(research_run_id, retry=True)

    assert initial_job_id == f"research-{research_run_id}"
    assert retry_job_id.startswith(f"research-{research_run_id}-retry-")
    assert retry_job_id != initial_job_id
    assert redis.job_ids == [initial_job_id, retry_job_id]
