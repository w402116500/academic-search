"""完整候选集合流式相关性分析 Worker。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.settings import get_literature_source_settings
from app.db.session import async_session_factory
from app.modules.workflow.candidate_relevance_execution import CandidateRelevanceRunExecutor
from app.modules.workflow.search_session import SearchSessionStore
from app.workers.queues import RELEVANCE_QUEUE_NAME
from app.workers.redis import redis_client_from_environment, redis_settings_from_environment


async def run_candidate_relevance(
    context: dict[str, Any],
    search_run_id: str,
    attempt_id: str,
) -> dict[str, str]:
    """执行一次完整集合相关性分析；尝试标识仅用于 ARQ 幂等任务键。"""
    try:
        run_id = UUID(search_run_id)
    except ValueError as exc:
        raise ValueError("arq 候选相关性任务缺少合法的 search_run_id。") from exc
    if not attempt_id:
        raise ValueError("arq 候选相关性任务缺少尝试标识。")

    async with async_session_factory() as session:
        redis = redis_client_from_environment()
        try:
            settings = get_literature_source_settings()
            executor = CandidateRelevanceRunExecutor(
                session=session,
                search_run_id=run_id,
                session_store=SearchSessionStore(
                    redis,
                    ttl_seconds=settings.search_session_ttl_seconds,
                ),
                literature_settings=settings,
            )
            return await executor.execute(arq_context=context)
        finally:
            await redis.aclose()


class WorkerSettings:
    """只消费 relevance 队列；不设置任务总时长，仅由流活动和取消控制结束。"""

    functions = [run_candidate_relevance]
    redis_settings = redis_settings_from_environment()
    queue_name = RELEVANCE_QUEUE_NAME
    max_jobs = 2
    max_tries = 1
    job_timeout = None
