"""真实 PostgreSQL、Redis 与多源 Provider 的检索运行验收测试。"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.core.settings import LiteratureSourceSettings, get_literature_source_settings
from app.infra.db.models.collection import ResearchCollection
from app.infra.db.models.user import User
from app.infra.db.models.workflow import ResearchPlan, SearchRun
from app.infra.db.repositories.search_runs import SqlAlchemySearchRunRepository
from app.infra.db.session import async_session_factory
from app.infra.redis.connection import redis_client_from_environment
from app.infra.redis.job_queues import ArqCandidateRelevanceJobQueue
from app.infra.redis.search_session import RedisSearchSessionStore
from app.modules.research.state import WorkspaceWorkflowStage
from app.modules.search.execution import SearchRunExecutor
from app.modules.search.providers.base import SearchProvider
from app.modules.search.providers.registry import ProviderRegistry
from app.modules.search.run_service import SearchRunService
from app.modules.search.state import SearchRunStatus


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_search_run_persists_progress_and_candidates() -> None:
    """真实来源结果应进入 Redis 快照，运行摘要应进入 PostgreSQL。"""
    if os.getenv("RUN_LIVE_SEARCH_RUN_TESTS") != "1":
        pytest.skip("仅在 RUN_LIVE_SEARCH_RUN_TESTS=1 时运行真实检索运行测试")

    settings = get_literature_source_settings().model_copy(
        update={"search_citation_enrichment_limit": 0}
    )
    providers = _enabled_sources(settings)
    enabled_sources = [provider.source.value for provider in providers]
    if not enabled_sources:
        pytest.skip("当前 .env 没有启用任何文献来源")

    user_id = uuid4()
    collection_id = uuid4()
    plan_id = uuid4()
    run_id = uuid4()
    session_key = f"academic-search:search-run:{run_id}"
    redis = redis_client_from_environment()

    try:
        async with async_session_factory() as session:
            # 真实测试使用随机数据并在 finally 中删除，避免污染开发库中的用户数据。
            user = User(
                id=user_id,
                email=f"live-search-{user_id}@example.invalid",
                display_name="Live Search Test",
                status="active",
            )
            collection = ResearchCollection(
                id=collection_id,
                owner_user_id=user_id,
                name="Live search test workspace",
                research_question="How does urban green space affect mental health?",
                status="active",
                workflow_stage=WorkspaceWorkflowStage.PLAN_REVIEW.value,
            )
            plan = ResearchPlan(
                id=plan_id,
                collection_id=collection_id,
                revision=1,
                raw_request="How does urban green space affect mental health?",
                status="confirmed",
                direction_options=[
                    {"id": "urban-green-space", "title": "Urban green space and mental health"}
                ],
                selected_direction_id="urban-green-space",
                scope={"confirmed": {"start_year": 2020, "end_year": 2026, "languages": ["en"]}},
                query_plan={
                    "selected_direction_id": "urban-green-space",
                    "queries": [
                        {
                            "provider": provider,
                            "query": "urban green space mental health",
                        }
                        for provider in enabled_sources
                    ],
                },
                model_snapshot={"provider": "live-test"},
                confirmed_at=datetime.now(UTC),
            )
            run = SearchRun(
                id=run_id,
                collection_id=collection_id,
                research_plan_id=plan_id,
                redis_session_key=session_key,
                status=SearchRunStatus.QUEUED.value,
                stage="dispatch",
                attempt_no=1,
                provider_summary={},
                candidate_counts={},
            )
            session.add_all([user, collection, plan, run])
            await session.commit()

            runs = SqlAlchemySearchRunRepository(session)
            claimed_run = await SearchRunService(runs).claim_run(run_id)
            assert claimed_run is not None
            executor = SearchRunExecutor(
                runs=runs,
                search_run=claimed_run,
                session_store=RedisSearchSessionStore(
                    redis,
                    ttl_seconds=settings.search_session_ttl_seconds,
                ),
                relevance_queue=ArqCandidateRelevanceJobQueue(),
                registry=ProviderRegistry(providers),
                max_concurrent_providers=settings.search_max_concurrent_providers,
            )
            result = await executor.execute()

            await session.refresh(run)
            snapshot = await RedisSearchSessionStore(
                redis,
                ttl_seconds=settings.search_session_ttl_seconds,
            ).read_snapshot(session_key)

            assert result["search_run_id"] == str(run_id)
            assert run.status in {
                SearchRunStatus.COMPLETED.value,
                SearchRunStatus.PARTIAL_FAILED.value,
            }
            assert snapshot is not None
            assert snapshot["run_id"] == str(run_id)
            assert snapshot["status"] == claimed_run.status
            assert snapshot["stage"] == "completed"
            assert isinstance(snapshot["candidates"], list)
            assert claimed_run.candidate_counts["raw_candidate_count"] >= 0

            print(
                json.dumps(
                    {
                        "run_id": str(run_id),
                        "enabled_sources": enabled_sources,
                        "status": claimed_run.status,
                        "provider_summary": claimed_run.provider_summary,
                        "candidate_counts": claimed_run.candidate_counts,
                    },
                    ensure_ascii=True,
                    indent=2,
                )
            )
    finally:
        await redis.delete(session_key, f"{session_key}:events")
        async with async_session_factory() as cleanup_session:
            user = await cleanup_session.get(User, user_id)
            if user is not None:
                await cleanup_session.delete(user)
                await cleanup_session.commit()
        await redis.aclose()


def _enabled_sources(settings: LiteratureSourceSettings) -> list[SearchProvider]:
    """返回已启用来源配置对应的 Provider，避免测试复制 Registry 选择逻辑。"""
    # 延迟导入避免普通单元测试加载所有外部 Provider 的 HTTP 配置。
    from app.modules.search.providers.registry import build_provider_registry

    return list(build_provider_registry(settings))
