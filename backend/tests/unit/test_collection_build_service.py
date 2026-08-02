"""研究集合确认构建、单篇失败与待确认移除的离线测试。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from app.db.models.collection import CollectionPaper, ResearchCollection
from app.db.models.document import Document, IngestionRun
from app.db.models.paper import Paper
from app.modules.collections.build_contracts import IngestionRunStatus
from app.modules.collections.build_service import ResearchCollectionBuildService
from app.modules.ingestion.job_queue import IngestionQueueError
from app.modules.workflow.state import WorkspaceWorkflowStage
from sqlalchemy.ext.asyncio import AsyncSession

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000701")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000702")
_PAPER_ID = UUID("00000000-0000-0000-0000-000000000703")
_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000704")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000705")
_LATEST_ADDED_RUN = object()


class FakeRow:
    """模拟 SQLAlchemy Row 的解包接口，避免离线测试连接 PostgreSQL。"""

    def __init__(self, value: tuple[object, ...]) -> None:
        self._value = value

    def _tuple(self) -> tuple[object, ...]:
        return self._value


class FakeResult:
    """同时覆盖查询多行、单行与标量迭代的最小结果替身。"""

    def __init__(self, values: list[object]) -> None:
        self._values = values

    def __iter__(self) -> Iterator[object]:
        return iter(self._values)

    def one_or_none(self) -> FakeRow | None:
        if not self._values:
            return None
        value = self._values[0]
        assert isinstance(value, FakeRow)
        return value


class FakeSession:
    """按调用顺序返回 ORM 查询结果，并记录服务写入的内存会话替身。"""

    def __init__(
        self,
        *,
        scalar_values: list[object | None],
        scalars_values: list[list[object]] | None = None,
        execute_values: list[list[object]] | None = None,
    ) -> None:
        self._scalar_values = iter(scalar_values)
        self._scalars_values = iter(scalars_values or [])
        self._execute_values = iter(execute_values or [])
        self.added: list[object] = []
        self.commit_count = 0

    async def scalar(self, _statement: object) -> object | None:
        value = next(self._scalar_values)
        if value is _LATEST_ADDED_RUN:
            return next(item for item in reversed(self.added) if isinstance(item, IngestionRun))
        return value

    async def scalars(self, _statement: object) -> list[object]:
        return next(self._scalars_values)

    async def execute(self, _statement: object) -> FakeResult:
        return FakeResult(next(self._execute_values))

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def commit(self) -> None:
        self.commit_count += 1


class FakeQueue:
    """记录 arq 投递，必要时只让指定文献模拟 Redis 不可用。"""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.enqueued_run_ids: list[UUID] = []

    async def enqueue_ingestion(self, ingestion_run_id: UUID) -> str:
        if self._fail:
            raise IngestionQueueError("test queue unavailable")
        self.enqueued_run_ids.append(ingestion_run_id)
        return f"ingestion-{ingestion_run_id}"


def _collection(*, stage: str = "screening") -> ResearchCollection:
    """创建属于当前测试用户的活动工作区。"""
    return ResearchCollection(
        id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        name="Build test collection",
        status="active",
        workflow_stage=stage,
    )


def _document() -> Document:
    """创建已经准入、但尚未进入 RAG Worker 的正式 PDF 记录。"""
    return Document(
        id=_DOCUMENT_ID,
        collection_id=_COLLECTION_ID,
        paper_id=_PAPER_ID,
        origin_kind="open_access",
        original_filename="article.pdf",
        media_type="application/pdf",
        byte_size=1_024,
        sha256="a" * 64,
        object_key="documents/test/article.pdf",
        source_url="https://example.test/article.pdf",
        access_rights="open_access",
    )


def _run(*, status: str = "pending", attempt_no: int = 1) -> IngestionRun:
    """创建带完整 JSON 配置的入库运行，避免依赖数据库默认值。"""
    run = IngestionRun(
        id=_RUN_ID,
        document_id=_DOCUMENT_ID,
        pipeline_version="rag-ingestion-v1",
        status=status,
        stage="parse",
        chunking_config={},
        embedding_config={"model": "test"},
        statistics={},
        attempt_no=attempt_no,
        is_current=False,
    )
    # SQLAlchemy 实际插入时由 PostgreSQL 填充该字段；内存替身需要显式模拟。
    run.created_at = datetime.now(UTC)
    return run


@pytest.mark.asyncio
async def test_build_promotes_all_pending_runs_before_dispatching_workers() -> None:
    """构建确认必须先让 pending 变为 queued，避免 Worker 读取旧状态后直接退出。"""
    collection = _collection()
    run = _run()
    session = FakeSession(
        scalar_values=[collection, run, collection],
        scalars_values=[[run]],
        execute_values=[[(IngestionRunStatus.QUEUED.value, False)]],
    )
    queue = FakeQueue()

    result = await ResearchCollectionBuildService(cast(AsyncSession, session), queue).build(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
    )

    assert run.status == IngestionRunStatus.QUEUED.value
    assert run.arq_job_id == f"ingestion-{_RUN_ID}"
    assert queue.enqueued_run_ids == [_RUN_ID]
    assert collection.workflow_stage == WorkspaceWorkflowStage.COLLECTION_BUILDING.value
    assert result.runs[0].status is IngestionRunStatus.QUEUED


@pytest.mark.asyncio
async def test_build_marks_only_the_unavailable_queue_run_as_failed() -> None:
    """Redis 投递失败不能伪装成成功，且必须留下可重试的单篇失败记录。"""
    collection = _collection()
    run = _run()
    session = FakeSession(
        scalar_values=[collection, run, collection],
        scalars_values=[[run]],
        execute_values=[[(IngestionRunStatus.FAILED.value, False)]],
    )

    result = await ResearchCollectionBuildService(
        cast(AsyncSession, session), FakeQueue(fail=True)
    ).build(owner_user_id=_OWNER_ID, collection_id=_COLLECTION_ID)

    assert run.status == IngestionRunStatus.FAILED.value
    assert run.error_code == "ingestion_queue_unavailable"
    assert result.runs[0].status is IngestionRunStatus.FAILED
    assert result.runs[0].arq_job_id is None
    assert collection.workflow_stage == WorkspaceWorkflowStage.FAILED.value


@pytest.mark.asyncio
async def test_retry_creates_new_queued_run_without_overwriting_failure() -> None:
    """重试必须递增 attempt_no 并保留原失败运行的错误信息。"""
    collection = _collection(stage="failed")
    previous = _run(status="failed", attempt_no=2)
    previous.error_code = "embedding_failed"
    session = FakeSession(
        scalar_values=[_LATEST_ADDED_RUN, collection],
        execute_values=[
            [FakeRow((collection, previous))],
            [(IngestionRunStatus.QUEUED.value, False)],
        ],
    )
    queue = FakeQueue()

    result = await ResearchCollectionBuildService(cast(AsyncSession, session), queue).retry_run(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        ingestion_run_id=_RUN_ID,
    )

    new_run = next(item for item in session.added if isinstance(item, IngestionRun))
    assert new_run.id != previous.id
    assert new_run.attempt_no == 3
    assert new_run.status == IngestionRunStatus.QUEUED.value
    assert previous.error_code == "embedding_failed"
    assert queue.enqueued_run_ids == [new_run.id]
    assert result.runs[0].ingestion_run_id == new_run.id


@pytest.mark.asyncio
async def test_remove_pending_document_archives_metadata_without_deleting_file() -> None:
    """移出待确认文献只改变 PostgreSQL 审计状态，不触碰 MinIO 正式对象。"""
    collection = _collection()
    collection_paper = CollectionPaper(collection_id=_COLLECTION_ID, paper_id=_PAPER_ID)
    collection_paper.tags = []
    collection_paper.added_at = datetime.now(UTC)
    document = _document()
    run = _run()
    session = FakeSession(
        scalar_values=[collection, run],
        execute_values=[[FakeRow((collection_paper, document))]],
    )

    result = await ResearchCollectionBuildService(
        cast(AsyncSession, session)
    ).remove_pending_document(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        document_id=_DOCUMENT_ID,
    )

    assert collection_paper.status == "archived"
    assert run.status == IngestionRunStatus.CANCELLED.value
    assert result.collection_paper_status == "archived"
    assert result.ingestion_run_status is IngestionRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_list_documents_counts_only_current_completed_runs_as_researchable() -> None:
    """页面可问答数量只统计已完成且 current 的版本，pending 不能提前计入。"""
    collection = _collection()
    collection_paper = CollectionPaper(collection_id=_COLLECTION_ID, paper_id=_PAPER_ID)
    collection_paper.tags = []
    collection_paper.added_at = datetime.now(UTC)
    document = _document()
    paper = Paper(
        id=_PAPER_ID,
        doi="10.1000/build.example",
        title="A build service paper",
        authors=[{"literal": "Ada Lovelace"}],
        citation_text="[1] A build service paper.",
        citation_provider="test",
    )
    run = _run(status="completed")
    run.is_current = True
    session = FakeSession(
        scalar_values=[collection],
        execute_values=[[FakeRow((collection_paper, paper, document, run))]],
    )

    response = await ResearchCollectionBuildService(cast(AsyncSession, session)).list_documents(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
    )

    assert response.summary.active_document_count == 1
    assert response.summary.researchable_document_count == 1
    assert response.summary.ingestion_status_counts == {IngestionRunStatus.COMPLETED: 1}
    assert response.documents[0].latest_ingestion_run is not None
