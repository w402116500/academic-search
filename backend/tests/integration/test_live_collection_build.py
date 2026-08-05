"""真实 PostgreSQL 的研究集合确认构建集成测试。"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from app.infra.db.models.collection import CollectionPaper, ResearchCollection
from app.infra.db.models.document import Document, IngestionRun
from app.infra.db.models.paper import Paper
from app.infra.db.models.user import User
from app.infra.db.repositories.collection_builds import SqlAlchemyCollectionBuildAdapter
from app.infra.db.session import async_session_factory
from app.modules.research.build_contracts import IngestionRunStatus
from app.modules.research.state import WorkspaceWorkflowStage

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_COLLECTION_BUILD_TESTS"


class LiveQueue:
    """真实数据库测试使用的队列替身，避免为状态事务测试启动真实 Worker。"""

    def __init__(self) -> None:
        self.enqueued_run_ids: list[UUID] = []

    async def enqueue_ingestion(self, ingestion_run_id: UUID) -> str:
        self.enqueued_run_ids.append(ingestion_run_id)
        return f"integration-ingestion-{ingestion_run_id}"


def _live_test_is_enabled() -> bool:
    """只有用户显式允许创建本地数据库临时记录时执行。"""
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_build_transitions_pending_document_and_records_arq_job() -> None:
    """确认集合应持久化 queued 状态、任务标识和工作区构建阶段。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行本地构建集成测试")

    owner_user_id = uuid4()
    collection_id = uuid4()
    paper_id = uuid4()
    document_id = uuid4()
    ingestion_run_id = uuid4()
    doi = f"10.9999/local-build-{uuid4().hex}"
    queue = LiveQueue()

    try:
        async with async_session_factory() as session:
            async with session.begin():
                session.add_all(
                    (
                        User(id=owner_user_id, display_name="Local build integration test user"),
                        ResearchCollection(
                            id=collection_id,
                            owner_user_id=owner_user_id,
                            name="Local build integration test collection",
                            workflow_stage=WorkspaceWorkflowStage.SCREENING.value,
                        ),
                        Paper(
                            id=paper_id,
                            doi=doi,
                            title="Local collection build integration test",
                            authors=[{"literal": "Ada Lovelace"}],
                            citation_text="[1] Local collection build integration test.",
                            citation_provider="integration-test",
                        ),
                        CollectionPaper(collection_id=collection_id, paper_id=paper_id),
                        Document(
                            id=document_id,
                            collection_id=collection_id,
                            paper_id=paper_id,
                            origin_kind="open_access",
                            original_filename="local-build.pdf",
                            media_type="application/pdf",
                            byte_size=1_024,
                            sha256="b" * 64,
                            object_key=f"tests/live-build/{document_id}.pdf",
                            source_url="https://example.test/local-build.pdf",
                            access_rights="open_access",
                        ),
                        IngestionRun(
                            id=ingestion_run_id,
                            document_id=document_id,
                            pipeline_version="rag-ingestion-v1",
                            status=IngestionRunStatus.PENDING.value,
                            stage="parse",
                            chunking_config={},
                            embedding_config={},
                            statistics={},
                            attempt_no=1,
                            is_current=False,
                        ),
                    )
                )

            response = await SqlAlchemyCollectionBuildAdapter(session, queue).build(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
            )
            run = await session.get(IngestionRun, ingestion_run_id)
            collection = await session.get(ResearchCollection, collection_id)

            assert run is not None
            assert collection is not None
            assert run.status == IngestionRunStatus.QUEUED.value
            assert run.arq_job_id == f"integration-ingestion-{ingestion_run_id}"
            assert queue.enqueued_run_ids == [ingestion_run_id]
            assert collection.workflow_stage == WorkspaceWorkflowStage.COLLECTION_BUILDING.value
            assert response.runs[0].status is IngestionRunStatus.QUEUED
    finally:
        async with async_session_factory() as cleanup_session:
            async with cleanup_session.begin():
                user = await cleanup_session.get(User, owner_user_id)
                if user is not None:
                    await cleanup_session.delete(user)
                    await cleanup_session.flush()

                paper = await cleanup_session.get(Paper, paper_id)
                if paper is not None:
                    await cleanup_session.delete(paper)
