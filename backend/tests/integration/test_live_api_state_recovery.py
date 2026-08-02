"""真实 API 的刷新恢复、Redis 过期与跨账号隔离验收测试。

测试只写入随机 UUID 的本地 PostgreSQL 和 Redis 数据，不调用模型、Provider、
MinIO 或 Milvus。运行结束会删除两个账号、工作区和对应 Redis 会话。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from app.api.routers import search_runs as search_run_router
from app.core.security import AuthenticationSettings, create_access_token
from app.core.settings import get_literature_source_settings
from app.db.models.collection import ResearchCollection
from app.db.models.user import User
from app.db.models.workflow import ResearchPlan, SearchRun
from app.db.session import async_session_factory
from app.main import app
from app.modules.search.citation_formatter import CitationFormat
from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateLinks,
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.workflow.search_session import (
    SearchSessionStore,
    build_search_event_stream_key,
    build_search_session_key,
)
from app.modules.workflow.state import (
    ResearchPlanStatus,
    SearchRunStage,
    SearchRunStatus,
    WorkspaceWorkflowStage,
)
from app.workers.redis import redis_client_from_environment
from pydantic import SecretStr

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_API_STATE_RECOVERY_TESTS"
_TEST_JWT_SECRET = "live-api-state-recovery-test-secret-key-2026"


def _live_test_is_enabled() -> bool:
    """只有显式允许时才对本地状态服务写入临时验收数据。"""
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


def _candidate(candidate_id: UUID) -> UnifiedCandidate:
    """构造一条由服务端 Redis 快照提供的、可展示候选。"""
    source_record = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id=f"live-api-{candidate_id}",
        title="API state recovery candidate",
        authors=(CandidateAuthor(name="Ada Lovelace"),),
        doi="10.9999/api-state-recovery",
    )
    return UnifiedCandidate(
        candidate_id=candidate_id,
        doi=source_record.doi,
        citation=CitationMetadata(
            status=CitationMetadataStatus.READY,
            authors=(CitationAuthor(given="Ada", family="Lovelace"),),
            title=source_record.title,
            document_type="journal_article",
            issued_date=CitationDate(year=2024, month=5, day=1),
            venue="Journal of API State Recovery",
            volume="12",
            pages="101-115",
            doi=source_record.doi,
            url="https://doi.org/10.9999/api-state-recovery",
        ),
        title=source_record.title,
        title_key="api state recovery candidate",
        authors=source_record.authors,
        links=CandidateLinks(landing_url="https://doi.org/10.9999/api-state-recovery"),
        source_records=(source_record,),
        triage=TriageDecision(included=True),
    )


def _authorization_header(user_id: UUID) -> dict[str, str]:
    """为真实 API 请求签发短期 JWT，不经由注册接口增加无关测试步骤。"""
    settings = AuthenticationSettings(auth_jwt_secret_key=SecretStr(_TEST_JWT_SECRET))
    token = create_access_token(user_id=user_id, settings=settings)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_api_recovers_search_state_and_hides_foreign_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """刷新可恢复状态；Redis 过期可辨识；另一账号无法读取任何任务数据。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实 API 验收")

    # 测试不依赖开发者是否已配置登录密钥；请求内认证仍走真实 JWT 校验依赖。
    monkeypatch.setenv("AUTH_JWT_SECRET_KEY", _TEST_JWT_SECRET)

    owner_user_id = uuid4()
    other_user_id = uuid4()
    collection_id = uuid4()
    plan_id = uuid4()
    run_id = uuid4()
    candidate_id = uuid4()
    event_plan_id = uuid4()
    event_run_id = uuid4()
    session_key = build_search_session_key(run_id)
    event_key = build_search_event_stream_key(run_id)
    event_session_key = build_search_session_key(event_run_id)
    event_stream_key = build_search_event_stream_key(event_run_id)
    redis = redis_client_from_environment()
    settings = get_literature_source_settings()

    try:
        async with async_session_factory() as session:
            session.add_all(
                (
                    User(
                        id=owner_user_id,
                        email=f"live-api-owner-{owner_user_id}@example.invalid",
                        display_name="Live API Owner",
                        status="active",
                    ),
                    User(
                        id=other_user_id,
                        email=f"live-api-other-{other_user_id}@example.invalid",
                        display_name="Live API Other",
                        status="active",
                    ),
                    ResearchCollection(
                        id=collection_id,
                        owner_user_id=owner_user_id,
                        name="Live API state recovery workspace",
                        research_question="How do urban parks affect wellbeing?",
                        status="active",
                        workflow_stage=WorkspaceWorkflowStage.SCREENING.value,
                    ),
                    ResearchPlan(
                        id=plan_id,
                        collection_id=collection_id,
                        revision=1,
                        raw_request="How do urban parks affect wellbeing?",
                        status=ResearchPlanStatus.CONFIRMED.value,
                        direction_options=[],
                        selected_direction_id="urban-parks",
                        scope={"confirmed": {"languages": ["en"]}},
                        query_plan={"selected_direction_id": "urban-parks", "queries": []},
                        model_snapshot={"provider": "live-api-state-recovery"},
                        confirmed_at=datetime.now(UTC),
                    ),
                    SearchRun(
                        id=run_id,
                        collection_id=collection_id,
                        research_plan_id=plan_id,
                        redis_session_key=session_key,
                        status=SearchRunStatus.PARTIAL_FAILED.value,
                        stage=SearchRunStage.COMPLETED.value,
                        attempt_no=1,
                        provider_summary={
                            "openalex": {"status": "completed"},
                            "semantic_scholar": {
                                "status": "failed",
                                "error": {"code": "remote_error", "retryable": True},
                            },
                        },
                        candidate_counts={"raw_candidate_count": 1, "included_candidate_count": 1},
                        finished_at=datetime.now(UTC),
                    ),
                )
            )
            await session.commit()

        store = SearchSessionStore(redis, ttl_seconds=settings.search_session_ttl_seconds)
        await store.write_snapshot(
            session_key,
            {
                "run_id": str(run_id),
                "status": SearchRunStatus.PARTIAL_FAILED.value,
                "stage": SearchRunStage.COMPLETED.value,
                "candidate_counts": {"raw_candidate_count": 1, "included_candidate_count": 1},
                "candidates": [_candidate(candidate_id).model_dump(mode="json")],
            },
        )
        await store.append_event(
            session_key,
            {
                "run_id": str(run_id),
                "status": SearchRunStatus.PARTIAL_FAILED.value,
                "stage": SearchRunStage.COMPLETED.value,
                "provider_summary": {"semantic_scholar": {"status": "failed"}},
                "candidate_counts": {"included_candidate_count": 1},
            },
        )

        owner_headers = _authorization_header(owner_user_id)
        other_headers = _authorization_header(other_user_id)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            workspace_response = await client.get(
                "/api/v1/collections?q=state", headers=owner_headers
            )
            assert workspace_response.status_code == 200
            assert [item["id"] for item in workspace_response.json()["items"]] == [
                str(collection_id)
            ]

            plan_response = await client.get(
                f"/api/v1/collections/{collection_id}/plan", headers=owner_headers
            )
            assert plan_response.status_code == 200
            assert plan_response.json()["status"] == "confirmed"

            run_response = await client.get(
                f"/api/v1/collections/{collection_id}/search-runs/current", headers=owner_headers
            )
            assert run_response.status_code == 200
            assert run_response.json()["id"] == str(run_id)
            assert run_response.json()["status"] == "partial_failed"
            assert run_response.json()["provider_summary"]["semantic_scholar"]["status"] == "failed"

            candidates_response = await client.get(
                f"/api/v1/collections/{collection_id}/search-runs/{run_id}/candidates",
                headers=owner_headers,
            )
            assert candidates_response.status_code == 200
            assert candidates_response.json()["candidates"][0]["candidate_id"] == str(candidate_id)

            citation_response = await client.get(
                (
                    f"/api/v1/collections/{collection_id}/search-runs/{run_id}/candidates/"
                    f"{candidate_id}/citation?format={CitationFormat.APA_7.value}"
                ),
                headers=owner_headers,
            )
            assert citation_response.status_code == 200
            assert citation_response.json()["format"] == CitationFormat.APA_7.value
            assert "api state recovery candidate" in citation_response.json()["text"].casefold()

            events_response = await client.get(
                f"/api/v1/collections/{collection_id}/search-runs/{run_id}/events",
                headers=owner_headers,
            )
            assert events_response.status_code == 200
            assert "event: snapshot" in events_response.text
            assert '"status": "partial_failed"' in events_response.text

            for path in (
                f"/api/v1/collections/{collection_id}",
                f"/api/v1/collections/{collection_id}/plan",
                f"/api/v1/collections/{collection_id}/search-runs/current",
                f"/api/v1/collections/{collection_id}/search-runs/{run_id}/candidates",
                (
                    f"/api/v1/collections/{collection_id}/search-runs/{run_id}/candidates/"
                    f"{candidate_id}/fulltext"
                ),
                (
                    f"/api/v1/collections/{collection_id}/search-runs/{run_id}/candidates/"
                    f"{candidate_id}/citation"
                ),
            ):
                response = await client.get(path, headers=other_headers)
                assert response.status_code == 404

            # 此运行模拟浏览器中途断线时仍在执行的任务；下一次连接从 0-0 获取
            # 已写入 Redis Stream 的进度，而不依赖浏览器内存中的动画状态。
            async with async_session_factory() as session:
                session.add_all(
                    (
                        ResearchPlan(
                            id=event_plan_id,
                            collection_id=collection_id,
                            revision=2,
                            raw_request="How do urban parks affect wellbeing?",
                            status=ResearchPlanStatus.READY.value,
                            direction_options=[],
                            scope={},
                            query_plan={},
                            model_snapshot={},
                        ),
                        SearchRun(
                            id=event_run_id,
                            collection_id=collection_id,
                            research_plan_id=event_plan_id,
                            redis_session_key=event_session_key,
                            status=SearchRunStatus.RUNNING.value,
                            stage=SearchRunStage.PROVIDER_SEARCH.value,
                            attempt_no=1,
                            provider_summary={"openalex": {"status": "running"}},
                            candidate_counts={"raw_candidate_count": 0},
                            started_at=datetime.now(UTC),
                        ),
                    )
                )
                await session.commit()

            event_id = await store.append_event(
                event_session_key,
                {
                    "run_id": str(event_run_id),
                    "status": SearchRunStatus.PARTIAL_FAILED.value,
                    "stage": SearchRunStage.COMPLETED.value,
                    "provider_summary": {"semantic_scholar": {"status": "failed"}},
                    "candidate_counts": {"included_candidate_count": 1},
                },
            )
            reconnect_response = await client.get(
                f"/api/v1/collections/{collection_id}/search-runs/{event_run_id}/events",
                headers={**owner_headers, "Last-Event-ID": "0-0"},
            )
            assert reconnect_response.status_code == 200
            assert f"id: {event_id}" in reconnect_response.text
            assert "event: progress" in reconnect_response.text

            class FakeSearchRunQueue:
                """记录重试投递，避免真实验收意外启动外部 Provider 请求。"""

                def __init__(self) -> None:
                    self.enqueued_run_ids: list[UUID] = []

                async def enqueue_search(self, search_run_id: UUID) -> str:
                    self.enqueued_run_ids.append(search_run_id)
                    return f"live-api-retry-{search_run_id}"

            retry_queue = FakeSearchRunQueue()
            monkeypatch.setattr(
                search_run_router,
                "ArqSearchRunJobQueue",
                lambda: retry_queue,
            )
            retry_response = await client.post(
                f"/api/v1/collections/{collection_id}/search-runs/{run_id}/retry",
                headers=owner_headers,
            )
            assert retry_response.status_code == 202
            assert retry_response.json()["attempt_no"] == 2
            retry_run_id = UUID(retry_response.json()["id"])
            assert retry_queue.enqueued_run_ids == [retry_run_id]

            refreshed_retry = await client.get(
                f"/api/v1/collections/{collection_id}/search-runs/current",
                headers=owner_headers,
            )
            assert refreshed_retry.status_code == 200
            assert refreshed_retry.json()["id"] == str(retry_run_id)
            assert refreshed_retry.json()["status"] == "queued"

            await redis.delete(session_key)
            expired_response = await client.get(
                f"/api/v1/collections/{collection_id}/search-runs/{run_id}/candidates",
                headers=owner_headers,
            )
            assert expired_response.status_code == 410
            assert expired_response.json()["detail"]["code"] == "search_run_session_expired"

        async with async_session_factory() as session:
            run = await session.get(SearchRun, run_id)
            assert run is not None
            assert run.status == SearchRunStatus.EXPIRED.value

        print(
            json.dumps(
                {
                    "workspace_id": str(collection_id),
                    "search_run_id": str(run_id),
                    "recovery": "confirmed_plan_and_partial_failed_run_visible",
                    "isolation": "foreign_requests_return_404",
                    "reconnect": "last_event_id_replays_progress_event",
                    "retry": "partial_failure_creates_queued_attempt_two",
                    "expiration": "candidates_return_410_and_run_becomes_expired",
                },
                ensure_ascii=True,
            )
        )
    finally:
        await redis.delete(session_key, event_key, event_session_key, event_stream_key)
        async with async_session_factory() as cleanup_session:
            for user_id in (owner_user_id, other_user_id):
                user = await cleanup_session.get(User, user_id)
                if user is not None:
                    await cleanup_session.delete(user)
            await cleanup_session.commit()
        await redis.aclose()
