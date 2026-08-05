"""多源文献检索 Worker。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.settings import get_literature_source_settings
from app.infra.db.repositories.search_runs import SqlAlchemySearchRunRepository
from app.infra.db.session import async_session_factory
from app.infra.redis.connection import redis_client_from_environment
from app.infra.redis.job_queues import ArqCandidateRelevanceJobQueue
from app.infra.redis.search_session import RedisSearchSessionStore
from app.modules.search.execution import SearchRunExecutor
from app.modules.search.providers.registry import build_provider_registry
from app.modules.search.run_service import SearchRunService
from app.modules.search.state import SearchRunStatus


async def run_search(_ctx: dict[str, Any], search_run_id: str) -> dict[str, str]:
    """领取检索运行并执行来源编排；重复或过期队列消息安全忽略。"""
    try:
        run_id = UUID(search_run_id)
    except ValueError as exc:
        raise ValueError("arq 文献检索任务缺少合法的 search_run_id。") from exc

    async with async_session_factory() as session:
        runs = SqlAlchemySearchRunRepository(session)
        workflow_service = SearchRunService(runs)
        search_run = await workflow_service.claim_run(run_id)
        if search_run is None:
            return {"search_run_id": str(run_id), "status": "ignored"}

        redis = redis_client_from_environment()
        try:
            settings = get_literature_source_settings()
            executor = SearchRunExecutor(
                runs=runs,
                search_run=search_run,
                session_store=RedisSearchSessionStore(
                    redis,
                    ttl_seconds=settings.search_session_ttl_seconds,
                ),
                relevance_queue=ArqCandidateRelevanceJobQueue(),
                registry=build_provider_registry(settings),
                max_concurrent_providers=settings.search_max_concurrent_providers,
            )
            return await executor.execute()
        except Exception as exc:
            # 任务边界必须留下失败状态，同时重新抛出让 arq 日志保留根因。
            await workflow_service.complete_run(
                search_run_id=run_id,
                status=SearchRunStatus.FAILED,
                provider_summary={},
                candidate_counts={},
                error_code="search_worker_unexpected_error",
                error_message="文献检索 Worker 发生未预期错误，请查看任务日志。",
            )
            raise RuntimeError("文献检索 Worker 执行失败。") from exc
        finally:
            await redis.aclose()
