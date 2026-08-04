"""真实 PostgreSQL、Redis 与 Worker 下的研究运行治理验收。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from app.db.models.collection import CollectionPaper, ResearchCollection
from app.db.models.document import Document, IngestionRun
from app.db.models.paper import Paper
from app.db.models.user import User
from app.db.models.workflow import ResearchPlan, SearchRun
from app.db.session import async_session_factory
from app.modules.collections.build_contracts import CollectionBuildError, CollectionBuildErrorCode
from app.modules.collections.build_service import ResearchCollectionBuildService
from app.modules.ingestion.settings import IngestionSettings
from app.modules.research.contracts import (
    CreateConversationRequest,
    ResearchError,
    ResearchErrorCode,
    ResearchRunStage,
    ResearchRunStatus,
)
from app.modules.research.events import ResearchEventStore
from app.modules.research.graph import OpenAICompatibleResearchModel, ResearchRouteDecision
from app.modules.research.service import ResearchConversationService
from app.modules.research.settings import ResearchSettings
from app.modules.workflow.contracts import SearchRunError, SearchRunErrorCode
from app.modules.workflow.search_run_service import SearchRunService
from app.modules.workflow.settings import WorkflowSettings, get_workflow_settings
from app.modules.workflow.state import ResearchPlanStatus, SearchRunStatus, WorkspaceWorkflowStage
from app.workers.redis import redis_client_from_environment
from app.workers.research import ResearchWorkerDependencies, run_research, startup
from sqlalchemy import delete, select

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_RESEARCH_GOVERNANCE_TESTS"


class CapturingResearchQueue:
    """让服务层创建真实运行记录，但不让外部 arq Worker 抢占本次验收任务。"""

    async def enqueue_research(self, research_run_id: UUID, *, retry: bool = False) -> str:
        suffix = "-retry" if retry else ""
        return f"live-research-governance-{research_run_id}{suffix}"


class UnexpectedSearchQueue:
    """配额拒绝必须在调用队列前发生，此替身可捕获意外投递。"""

    async def enqueue_search(self, search_run_id: UUID) -> str:
        raise AssertionError(f"超额检索不应进入队列：{search_run_id}")


class UnexpectedIngestionQueue:
    """批量或全局超额的入库运行不应对 Redis 产生任何投递。"""

    async def enqueue_ingestion(self, ingestion_run_id: UUID) -> str:
        raise AssertionError(f"超额入库不应进入队列：{ingestion_run_id}")


class BlockingRouterModel:
    """在首次路由模型调用中制造可取消窗口，不访问外部模型。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def route_question(self, question: str) -> ResearchRouteDecision:
        assert question
        self.started.set()
        await self.release.wait()
        return ResearchRouteDecision(
            mode="single_rag",
            reason="当前问题可由同一组已授权原文直接核验。",
        )

    async def rewrite_query(self, question: str) -> str:
        raise AssertionError(f"取消后不应继续改写查询：{question}")

    async def generate_answer(self, **_: object) -> object:
        raise AssertionError("取消后不应生成回答。")

    async def plan_subquestions(self, **_: object) -> tuple[str, ...]:
        raise AssertionError("取消后不应规划子问题。")

    async def decide_research_action(self, **_: object) -> object:
        raise AssertionError("取消后不应继续控制循环。")

    async def verify_evidence(self, **_: object) -> object:
        raise AssertionError("取消后不应核验证据。")

    async def verify_answer_claims(self, **_: object) -> object:
        raise AssertionError("取消后不应核验回答主张。")


def _live_test_is_enabled() -> bool:
    """真实数据库与 Redis 写入只能由显式环境变量开启。"""
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_worker_confirms_cancellation_and_enforces_daily_governance() -> None:
    """已领取运行在模型返回边界停止，且不产生答案、证据或伪终态。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实研究治理验收")

    owner_user_id, collection_id, paper_id, document_id, ingestion_run_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    research_run_id: UUID | None = None
    conversation_id: UUID | None = None
    worker_task: asyncio.Task[dict[str, str | int]] | None = None
    model = BlockingRouterModel()
    settings = ResearchSettings(
        rag_user_daily_research_run_limit=1,
        rag_global_daily_research_run_limit=100,
    )

    try:
        async with async_session_factory() as session:
            async with session.begin():
                session.add_all(
                    (
                        User(id=owner_user_id, display_name="Live governance test user"),
                        ResearchCollection(
                            id=collection_id,
                            owner_user_id=owner_user_id,
                            name="Live governance collection",
                            workflow_stage=WorkspaceWorkflowStage.RESEARCHING.value,
                        ),
                        Paper(
                            id=paper_id,
                            doi=f"10.48550/live-governance-{uuid4().hex}",
                            title="Governance fixture paper",
                            authors=[{"literal": "Test Author"}],
                            abstract="A completed indexed document for research run governance.",
                            publication_year=2026,
                            paper_type="other",
                            citation_text="Test Author. Governance fixture paper. 2026.",
                            citation_provider="test",
                        ),
                    )
                )
                session.add(
                    CollectionPaper(
                        collection_id=collection_id,
                        paper_id=paper_id,
                        status="active",
                        tags=[],
                    )
                )
                session.add(
                    Document(
                        id=document_id,
                        collection_id=collection_id,
                        paper_id=paper_id,
                        origin_kind="open_access",
                        original_filename="governance-fixture.pdf",
                        media_type="application/pdf",
                        byte_size=64,
                        sha256=hashlib.sha256(b"live-governance").hexdigest(),
                        object_key=f"live-governance/{document_id}.pdf",
                        access_rights="open_access",
                    )
                )
                session.add(
                    IngestionRun(
                        id=ingestion_run_id,
                        document_id=document_id,
                        pipeline_version="live-governance-v1",
                        status="completed",
                        stage="index",
                        chunking_config={"source": "live governance fixture"},
                        embedding_config={"source": "not invoked after cancellation"},
                        statistics={},
                        attempt_no=1,
                        is_current=True,
                        started_at=datetime.now(UTC),
                        finished_at=datetime.now(UTC),
                    )
                )

        queue = CapturingResearchQueue()
        async with async_session_factory() as session:
            service = ResearchConversationService(session, queue, settings=settings)
            conversation = await service.create_conversation(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                request=CreateConversationRequest(title="治理验收"),
            )
            conversation_id = conversation.id
            asked = await service.ask_question(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                conversation_id=conversation.id,
                content="请根据当前集合核验这篇文献的主题。",
                model_config=get_workflow_settings().model_snapshot,
            )
            research_run_id = asked.research_run.id

        worker_context: dict[str, Any] = {}
        await startup(worker_context)
        dependencies = cast(ResearchWorkerDependencies, worker_context["research_dependencies"])
        worker_context["research_dependencies"] = ResearchWorkerDependencies(
            ingestion_settings=dependencies.ingestion_settings,
            research_settings=settings,
            workflow_settings=dependencies.workflow_settings,
            embedder=dependencies.embedder,
            vector_search=dependencies.vector_search,
            reranker=None,
            model=cast(OpenAICompatibleResearchModel, model),
        )
        worker_task = asyncio.create_task(run_research(worker_context, str(research_run_id)))
        await asyncio.wait_for(model.started.wait(), timeout=10)

        async with async_session_factory() as session:
            cancelled = await ResearchConversationService(session, settings=settings).cancel_run(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                conversation_id=conversation_id,
                research_run_id=research_run_id,
            )
        assert cancelled.status is ResearchRunStatus.RUNNING
        assert cancelled.cancel_requested_at is not None

        model.release.set()
        outcome = await asyncio.wait_for(worker_task, timeout=15)
        assert outcome["status"] == ResearchRunStatus.CANCELLED.value

        async with async_session_factory() as session:
            service = ResearchConversationService(session, settings=settings)
            run = await service.get_run(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                conversation_id=conversation_id,
                research_run_id=research_run_id,
            )
            assert run.status is ResearchRunStatus.CANCELLED
            assert run.stage is ResearchRunStage.CANCELLED
            assert run.output_message_id is None
            assert run.evidences == []
            assert run.retrieval_trace["cancellation"]["state"] == "confirmed"
            timing = cast(dict[str, object], run.retrieval_trace["timing"])
            stages = cast(list[dict[str, object]], timing["stages"])
            assert stages[-1]["stage"] == ResearchRunStage.PREPARING.value
            assert isinstance(timing["total_duration_ms"], int)

        redis = redis_client_from_environment()
        try:
            events = await ResearchEventStore(
                redis, ttl_seconds=settings.rag_event_ttl_seconds
            ).read_events(research_run_id, last_event_id="0-0", block_milliseconds=1)
        finally:
            await redis.aclose()
        assert any(event[1].get("status") == ResearchRunStatus.CANCELLED.value for event in events)

        async with async_session_factory() as session:
            user_limited = ResearchConversationService(session, queue, settings=settings)
            with pytest.raises(ResearchError) as user_error:
                await user_limited.ask_question(
                    owner_user_id=owner_user_id,
                    collection_id=collection_id,
                    conversation_id=conversation_id,
                    content="这条问题应被用户每日额度拒绝。",
                    model_config={},
                )
            assert user_error.value.code is ResearchErrorCode.USER_QUOTA_EXCEEDED

            global_limited = ResearchConversationService(
                session,
                queue,
                settings=ResearchSettings(
                    rag_user_daily_research_run_limit=100,
                    rag_global_daily_research_run_limit=1,
                ),
            )
            with pytest.raises(ResearchError) as global_error:
                await global_limited.ask_question(
                    owner_user_id=owner_user_id,
                    collection_id=collection_id,
                    conversation_id=conversation_id,
                    content="这条问题应被全局每日预算拒绝。",
                    model_config={},
                )
            assert global_error.value.code is ResearchErrorCode.GLOBAL_BUDGET_EXHAUSTED
    finally:
        model.release.set()
        if worker_task is not None and not worker_task.done():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(worker_task, timeout=15)
        if research_run_id is not None:
            redis = redis_client_from_environment()
            try:
                await redis.delete(ResearchEventStore.stream_key(research_run_id))
            finally:
                await redis.aclose()
        async with async_session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(ResearchCollection).where(ResearchCollection.id == collection_id)
                )
                await session.execute(delete(Paper).where(Paper.id == paper_id))
                await session.execute(delete(User).where(User.id == owner_user_id))


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_postgresql_rejects_search_and_ingestion_submission_budget_overages() -> None:
    """真实持久化计数同时保护检索、批量构建和失败入库重试的提交边界。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实研究治理验收")

    owner_user_id, collection_id, plan_id = uuid4(), uuid4(), uuid4()
    paper_ids = [uuid4() for _ in range(3)]
    document_ids = [uuid4() for _ in range(3)]
    pending_run_ids = [uuid4(), uuid4()]

    try:
        async with async_session_factory() as session:
            async with session.begin():
                session.add(
                    User(
                        id=owner_user_id,
                        display_name="Live submission budget test user",
                    )
                )
                session.add(
                    ResearchCollection(
                        id=collection_id,
                        owner_user_id=owner_user_id,
                        name="Live submission budget collection",
                        workflow_stage=WorkspaceWorkflowStage.PLAN_REVIEW.value,
                    )
                )
                session.add(
                    ResearchPlan(
                        id=plan_id,
                        collection_id=collection_id,
                        revision=1,
                        raw_request="验证运行额度的真实持久化计数。",
                        status=ResearchPlanStatus.CONFIRMED.value,
                        direction_options=[{"id": "governance", "title": "运行治理"}],
                        selected_direction_id="governance",
                        scope={"confirmed": {"languages": ["zh"]}},
                        query_plan={"queries": [{"provider": "openalex", "query": "governance"}]},
                        model_snapshot={},
                        confirmed_at=datetime.now(UTC),
                    )
                )
                session.add(
                    SearchRun(
                        id=uuid4(),
                        collection_id=collection_id,
                        research_plan_id=plan_id,
                        status=SearchRunStatus.FAILED.value,
                        stage="completed",
                        attempt_no=1,
                        provider_summary={},
                        candidate_counts={},
                        finished_at=datetime.now(UTC),
                    )
                )
                for index, (paper_id, document_id) in enumerate(
                    zip(paper_ids, document_ids, strict=True)
                ):
                    session.add(
                        Paper(
                            id=paper_id,
                            doi=f"10.48550/live-submission-{uuid4().hex}",
                            title=f"Submission budget fixture {index}",
                            authors=[{"literal": "Test Author"}],
                            abstract="Fixture for persistent daily submission budget checks.",
                            publication_year=2026,
                            paper_type="other",
                            citation_text=f"Test Author. Submission budget fixture {index}. 2026.",
                            citation_provider="test",
                        )
                    )
                    session.add(
                        CollectionPaper(
                            collection_id=collection_id,
                            paper_id=paper_id,
                            status="active",
                            tags=[],
                        )
                    )
                    session.add(
                        Document(
                            id=document_id,
                            collection_id=collection_id,
                            paper_id=paper_id,
                            origin_kind="open_access",
                            original_filename=f"submission-{index}.pdf",
                            media_type="application/pdf",
                            byte_size=64,
                            sha256=hashlib.sha256(f"submission-{index}".encode()).hexdigest(),
                            object_key=f"live-submission/{document_id}.pdf",
                            access_rights="open_access",
                        )
                    )
                session.add_all(
                    (
                        IngestionRun(
                            id=pending_run_ids[0],
                            document_id=document_ids[0],
                            pipeline_version="live-submission-v1",
                            status="pending",
                            stage="parse",
                            chunking_config={},
                            embedding_config={},
                            statistics={},
                            attempt_no=1,
                            is_current=False,
                        ),
                        IngestionRun(
                            id=pending_run_ids[1],
                            document_id=document_ids[1],
                            pipeline_version="live-submission-v1",
                            status="pending",
                            stage="parse",
                            chunking_config={},
                            embedding_config={},
                            statistics={},
                            attempt_no=1,
                            is_current=False,
                        ),
                        IngestionRun(
                            id=uuid4(),
                            document_id=document_ids[2],
                            pipeline_version="live-submission-v1",
                            status="failed",
                            stage="parse",
                            chunking_config={},
                            embedding_config={},
                            statistics={},
                            attempt_no=1,
                            is_current=False,
                            submitted_at=datetime.now(UTC),
                            finished_at=datetime.now(UTC),
                        ),
                    )
                )

        async with async_session_factory() as session:
            with pytest.raises(SearchRunError) as user_error:
                await SearchRunService(
                    session,
                    UnexpectedSearchQueue(),
                    settings=WorkflowSettings.model_construct(
                        workflow_user_daily_search_run_limit=1,
                        workflow_global_daily_search_run_limit=100,
                    ),
                ).start_search(owner_user_id=owner_user_id, collection_id=collection_id)
            assert user_error.value.code is SearchRunErrorCode.USER_QUOTA_EXCEEDED
            await session.rollback()

        async with async_session_factory() as session:
            with pytest.raises(SearchRunError) as global_error:
                await SearchRunService(
                    session,
                    UnexpectedSearchQueue(),
                    settings=WorkflowSettings.model_construct(
                        workflow_user_daily_search_run_limit=100,
                        workflow_global_daily_search_run_limit=1,
                    ),
                ).start_search(owner_user_id=owner_user_id, collection_id=collection_id)
            assert global_error.value.code is SearchRunErrorCode.GLOBAL_BUDGET_EXHAUSTED
            await session.rollback()

        async with async_session_factory() as session:
            with pytest.raises(CollectionBuildError) as user_error:
                await ResearchCollectionBuildService(
                    session,
                    UnexpectedIngestionQueue(),
                    settings=IngestionSettings.model_construct(
                        rag_user_daily_ingestion_run_limit=1,
                        rag_global_daily_ingestion_run_limit=100,
                    ),
                ).build(owner_user_id=owner_user_id, collection_id=collection_id)
            assert user_error.value.code is CollectionBuildErrorCode.USER_QUOTA_EXCEEDED
            await session.rollback()

        async with async_session_factory() as session:
            with pytest.raises(CollectionBuildError) as global_error:
                await ResearchCollectionBuildService(
                    session,
                    UnexpectedIngestionQueue(),
                    settings=IngestionSettings.model_construct(
                        rag_user_daily_ingestion_run_limit=100,
                        rag_global_daily_ingestion_run_limit=1,
                    ),
                ).build(owner_user_id=owner_user_id, collection_id=collection_id)
            assert global_error.value.code is CollectionBuildErrorCode.GLOBAL_BUDGET_EXHAUSTED
            await session.rollback()

        async with async_session_factory() as session:
            pending_runs = list(
                await session.scalars(
                    select(IngestionRun)
                    .where(IngestionRun.id.in_(pending_run_ids))
                    .order_by(IngestionRun.id)
                )
            )
            assert len(pending_runs) == 2
            assert all(run.status == "pending" for run in pending_runs)
            assert all(run.submitted_at is None for run in pending_runs)
    finally:
        async with async_session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(ResearchCollection).where(ResearchCollection.id == collection_id)
                )
                await session.execute(delete(Paper).where(Paper.id.in_(paper_ids)))
                await session.execute(delete(User).where(User.id == owner_user_id))
