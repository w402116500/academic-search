"""入库仓储事务边界的离线回归测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest
from app.infra.db.repositories.ingestion import SqlAlchemyIngestionRepository
from app.modules.rag.ingestion.contracts import IngestionContext, IngestionError, IngestionErrorCode
from sqlalchemy.exc import InvalidRequestError

_RUN_ID = UUID("00000000-0000-0000-0000-000000000401")
_OWNER_ID = UUID("00000000-0000-0000-0000-000000000402")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000403")
_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000404")
_CHUNK_ID = UUID("00000000-0000-0000-0000-000000000405")


class LockedRunRow:
    """兼容两处 SQLAlchemy 行读取方式的最小查询结果替身。"""

    def __init__(
        self,
        run: SimpleNamespace,
        document: SimpleNamespace,
        collection: SimpleNamespace,
    ) -> None:
        self._run = run
        self._document = document
        self._collection = collection

    def __iter__(self):
        return iter((self._run, self._document, self._collection))

    def _tuple(self) -> tuple[SimpleNamespace, SimpleNamespace]:
        return self._run, self._collection


class TransactionAwareSession:
    """模拟 SQLAlchemy 查询隐式开事务的最小会话。"""

    def __init__(self) -> None:
        self.in_transaction = False
        self.rollback_calls = 0
        self.begin_calls = 0
        self.run = SimpleNamespace(
            status="running",
            cancel_requested_at=None,
            embedding_config={},
            statistics={},
            stage="embed",
            is_current=True,
            error_code=None,
            error_message=None,
            finished_at=None,
        )
        self.collection = SimpleNamespace(status="active")
        self.document = SimpleNamespace(
            id=_DOCUMENT_ID,
            object_key="tests/transaction-boundary.pdf",
        )
        self.chunks = (SimpleNamespace(id=_CHUNK_ID, level=3, content="可被向量化的 L3 原文。"),)

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[TransactionAwareSession]:
        """若上一轮查询未清理事务，模拟真实 AsyncSession 抛出的异常。"""
        if self.in_transaction:
            raise InvalidRequestError("A transaction is already begun on this Session.")
        self.in_transaction = True
        self.begin_calls += 1
        try:
            yield self
        finally:
            self.in_transaction = False

    async def scalars(self, _statement: object) -> tuple[SimpleNamespace, ...]:
        """真实 AsyncSession 的首次 SELECT 同样会进入自动事务。"""
        self.in_transaction = True
        return self.chunks

    async def scalar(self, _statement: object) -> SimpleNamespace:
        """为仓储状态更新返回正在处理的运行记录。"""
        return self.run

    async def execute(self, _statement: object) -> SimpleNamespace:
        """为带工作区围栏的运行锁查询返回当前运行与活动集合。"""
        row = LockedRunRow(self.run, self.document, self.collection)
        return SimpleNamespace(one_or_none=lambda: row)

    async def rollback(self) -> None:
        """记录仓储是否在只读结果物化后显式结束事务。"""
        self.rollback_calls += 1
        self.in_transaction = False


def _context() -> IngestionContext:
    """构造完整入库上下文，避免测试依赖 PostgreSQL。"""
    return IngestionContext(
        ingestion_run_id=_RUN_ID,
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        document_id=_DOCUMENT_ID,
        object_key="tests/transaction-boundary.pdf",
        retrying=False,
    )


@pytest.mark.asyncio
async def test_loading_l3_chunks_closes_implicit_read_transaction_before_write_stage() -> None:
    """读取 L3 块后，嵌入记录和失败状态都必须还能开启各自的写事务。"""
    session = TransactionAwareSession()
    repository = SqlAlchemyIngestionRepository(session)  # type: ignore[arg-type]

    chunks = await repository.load_vector_chunks(_context())
    await repository.record_embedding(
        _RUN_ID,
        embedding_config={"model": "test-embedding"},
        vector_dimension=1024,
    )
    await repository.mark_failed(
        _RUN_ID,
        IngestionError(IngestionErrorCode.PERSISTENCE_FAILED, "用于验证失败状态回写。"),
    )

    assert [chunk.chunk_id for chunk in chunks] == [_CHUNK_ID]
    assert session.rollback_calls == 1
    assert session.begin_calls == 2
    assert session.run.embedding_config == {"model": "test-embedding", "vector_dimension": 1024}
    assert session.run.stage == "index"
    assert session.run.status == "failed"
    assert session.run.is_current is False


@pytest.mark.asyncio
async def test_claiming_a_cancelled_run_surfaces_a_cancelled_worker_result() -> None:
    """删除围栏取消排队任务后，Worker 不能把它作为幂等完成返回。"""
    session = TransactionAwareSession()
    session.run.status = "cancelled"
    repository = SqlAlchemyIngestionRepository(session)  # type: ignore[arg-type]

    with pytest.raises(IngestionError) as raised:
        await repository.claim(_RUN_ID)

    assert raised.value.code is IngestionErrorCode.CANCELLED
