"""完整候选集合流式相关性分析 Worker。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.fulltext_settings import get_fulltext_acquisition_settings
from app.core.settings import get_literature_source_settings
from app.core.workflow_settings import get_workflow_settings
from app.infra.db.repositories.search_runs import SqlAlchemySearchRunRepository
from app.infra.db.session import async_session_factory
from app.infra.llm.candidate_relevance import build_candidate_relevance_evaluator
from app.infra.redis.connection import (
    redis_client_from_environment,
    redis_settings_from_environment,
)
from app.infra.redis.job_queues import ArqCandidateRelevanceJobQueue
from app.infra.redis.queues import RELEVANCE_QUEUE_NAME
from app.infra.redis.search_session import RedisSearchSessionStore
from app.modules.documents.acquisition import OpenAccessPdfAvailabilityProbe
from app.modules.search.citation_enrichment import CitationMetadataEnricher
from app.modules.search.providers.doi_resolver import DoiMetadataResolver
from app.modules.search.relevance_execution import CandidateRelevanceRunExecutor


async def run_candidate_relevance(
    context: dict[str, Any],
    search_run_id: str,
    attempt_no: int,
) -> dict[str, str]:
    """执行一次完整集合相关性分析；尝试序号用于 Worker 自动恢复的幂等任务键。"""
    try:
        run_id = UUID(search_run_id)
    except ValueError as exc:
        raise ValueError("arq 候选相关性任务缺少合法的 search_run_id。") from exc
    if attempt_no not in {1, 2}:
        raise ValueError("arq 候选相关性任务缺少合法尝试序号。")

    async with async_session_factory() as session:
        redis = redis_client_from_environment()
        try:
            settings = get_literature_source_settings()
            workflow_settings = get_workflow_settings()
            executor = CandidateRelevanceRunExecutor(
                runs=SqlAlchemySearchRunRepository(session),
                search_run_id=run_id,
                session_store=RedisSearchSessionStore(
                    redis,
                    ttl_seconds=settings.search_session_ttl_seconds,
                ),
                citation_enrichment_limit=settings.search_citation_enrichment_limit,
                citation_enricher=CitationMetadataEnricher(
                    DoiMetadataResolver(settings.doi_resolver)
                ),
                pdf_availability_probe=OpenAccessPdfAvailabilityProbe(
                    get_fulltext_acquisition_settings()
                ),
                attempt_no=attempt_no,
                relevance_queue=ArqCandidateRelevanceJobQueue(),
                evaluator=build_candidate_relevance_evaluator(workflow_settings),
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
