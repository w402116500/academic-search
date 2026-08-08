"""工作区删除仓储事务边界的离线回归测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from app.infra.db.repositories.workspace_deletion import (
    SqlAlchemyWorkspaceDeletionRepository,
)
from sqlalchemy.ext.asyncio import AsyncSession

_OWNER_ID = UUID("00000000-0000-0000-0000-000000001101")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000001102")
_RUN_ONE_ID = UUID("00000000-0000-0000-0000-000000001103")
_RUN_TWO_ID = UUID("00000000-0000-0000-0000-000000001104")


class FakeWorkspaceDeletionSession:
    """模拟认证读取已让请求级 AsyncSession 进入 autobegin 事务。"""

    def __init__(
        self,
        *,
        scalar_values: list[object | None] | None = None,
        scalars_values: list[list[object]] | None = None,
        scalar_error: Exception | None = None,
        in_transaction: bool = True,
    ) -> None:
        self._scalar_values = iter(scalar_values or [])
        self._scalars_values = iter(scalars_values or [])
        self._scalar_error = scalar_error
        self._in_transaction = in_transaction
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.execute_statements: list[object] = []
        self.deleted: list[object] = []

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeWorkspaceDeletionSession]:
        self.begin_count += 1
        if self._in_transaction:
            raise AssertionError("已有事务时不应再次调用 session.begin()")
        self._in_transaction = True
        try:
            yield self
        finally:
            self._in_transaction = False

    def in_transaction(self) -> bool:
        return self._in_transaction

    async def scalar(self, _statement: object) -> object | None:
        if self._scalar_error is not None:
            raise self._scalar_error
        try:
            return next(self._scalar_values)
        except StopIteration as exc:
            raise AssertionError("测试缺少数据库 scalar 查询预设值") from exc

    async def scalars(self, _statement: object) -> list[object]:
        try:
            return next(self._scalars_values)
        except StopIteration as exc:
            raise AssertionError("测试缺少数据库 scalars 查询预设值") from exc

    async def execute(self, statement: object) -> None:
        self.execute_statements.append(statement)

    async def delete(self, instance: object) -> None:
        self.deleted.append(instance)

    async def commit(self) -> None:
        self.commit_count += 1
        self._in_transaction = False

    async def rollback(self) -> None:
        self.rollback_count += 1
        self._in_transaction = False


@pytest.mark.asyncio
async def test_begin_deletion_reuses_autobegun_request_transaction() -> None:
    """认证依赖已查库时，删除围栏应复用并提交请求级事务。"""
    collection = SimpleNamespace(status="active")
    session = FakeWorkspaceDeletionSession(
        scalar_values=[collection],
        scalars_values=[
            [_RUN_ONE_ID, _RUN_TWO_ID],
            ["documents/one.pdf", "documents/two.pdf"],
        ],
    )
    repository = SqlAlchemyWorkspaceDeletionRepository(cast(AsyncSession, session))

    snapshot = await repository.begin_deletion(
        owner_user_id=_OWNER_ID,
        workspace_id=_COLLECTION_ID,
    )

    assert snapshot is not None
    assert snapshot.ingestion_run_ids == (_RUN_ONE_ID, _RUN_TWO_ID)
    assert snapshot.document_object_keys == ("documents/one.pdf", "documents/two.pdf")
    assert collection.status == "deleting"
    assert session.begin_count == 0
    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert len(session.execute_statements) == 6


@pytest.mark.asyncio
async def test_delete_root_reuses_autobegun_request_transaction() -> None:
    """外部清理完成后的根记录删除也不能再次嵌套开启事务。"""
    collection = SimpleNamespace(status="deleting")
    session = FakeWorkspaceDeletionSession(scalar_values=[collection])
    repository = SqlAlchemyWorkspaceDeletionRepository(cast(AsyncSession, session))

    deleted = await repository.delete_root(
        owner_user_id=_OWNER_ID,
        workspace_id=_COLLECTION_ID,
    )

    assert deleted is True
    assert session.begin_count == 0
    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert session.deleted == [collection]
    assert len(session.execute_statements) == 2


@pytest.mark.asyncio
async def test_autobegun_write_transaction_rolls_back_on_failure() -> None:
    """复用既有事务时，数据库异常必须回滚并继续向上抛出。"""
    session = FakeWorkspaceDeletionSession(scalar_error=RuntimeError("db unavailable"))
    repository = SqlAlchemyWorkspaceDeletionRepository(cast(AsyncSession, session))

    with pytest.raises(RuntimeError, match="db unavailable"):
        await repository.delete_root(
            owner_user_id=_OWNER_ID,
            workspace_id=_COLLECTION_ID,
        )

    assert session.begin_count == 0
    assert session.commit_count == 0
    assert session.rollback_count == 1
