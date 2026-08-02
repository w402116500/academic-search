"""研究工作区所有权和生命周期的离线服务测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from app.db.models.collection import ResearchCollection
from app.modules.collections.workspace_contracts import (
    CreateWorkspaceRequest,
    UpdateWorkspaceRequest,
    WorkspaceError,
    WorkspaceErrorCode,
)
from app.modules.collections.workspace_service import ResearchWorkspaceService
from sqlalchemy.ext.asyncio import AsyncSession

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000201")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000202")


class FakeSession:
    """工作区服务所需的最小异步会话替身。"""

    def __init__(
        self,
        scalar_values: list[object | None] | None = None,
        scalar_batches: list[list[object]] | None = None,
    ) -> None:
        self._scalar_values = iter(scalar_values or [])
        self._scalar_batches = iter(scalar_batches or [])
        self.added: list[object] = []
        self.flush_count = 0
        self.commit_count = 0

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self

    async def scalar(self, _statement: object) -> object | None:
        return next(self._scalar_values)

    async def scalars(self, _statement: object) -> list[object]:
        return next(self._scalar_batches)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1

    async def commit(self) -> None:
        self.commit_count += 1

    async def refresh(self, _instance: object) -> None:
        return None


def _collection(*, status: str = "active") -> ResearchCollection:
    """构建一个已属于测试用户的工作区。"""
    return ResearchCollection(
        id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        name="Original name",
        description="Original description",
        status=status,
    )


def _listed_collection(*, collection_id: UUID, updated_at: datetime) -> ResearchCollection:
    """构建具有稳定分页排序键的工作区。"""
    collection = _collection()
    collection.id = collection_id
    collection.updated_at = updated_at
    return collection


@pytest.mark.asyncio
async def test_create_assigns_the_owner_from_the_service_boundary() -> None:
    """客户端请求中没有 owner 字段，服务只接受认证依赖传入的用户标识。"""
    session = FakeSession()
    service = ResearchWorkspaceService(cast(AsyncSession, session))

    created = await service.create(
        owner_user_id=_OWNER_ID,
        request=CreateWorkspaceRequest(name="  Green   space\nresearch  ", description="  Notes  "),
    )

    assert session.added == [created]
    assert created.owner_user_id == _OWNER_ID
    assert created.name == "Green space research"
    assert created.description == "Notes"
    assert created.status == "active"


@pytest.mark.asyncio
async def test_update_can_clear_description_but_rejects_archived_workspace() -> None:
    """显式 null 用于清空说明，归档后的普通编辑必须失败。"""
    active = _collection()
    session = FakeSession([active])
    service = ResearchWorkspaceService(cast(AsyncSession, session))

    updated = await service.update(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        request=UpdateWorkspaceRequest(description=None),
    )

    assert updated.description is None
    assert session.commit_count == 1

    archived_session = FakeSession([_collection(status="archived")])
    with pytest.raises(WorkspaceError) as error:
        await ResearchWorkspaceService(cast(AsyncSession, archived_session)).update(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            request=UpdateWorkspaceRequest(name="Renamed"),
        )
    assert error.value.code is WorkspaceErrorCode.NOT_ACTIVE


@pytest.mark.asyncio
async def test_archive_and_restore_are_idempotent() -> None:
    """重复调用生命周期操作不应使前端重试变成错误。"""
    active = _collection()
    archive_session = FakeSession([active])
    archived = await ResearchWorkspaceService(cast(AsyncSession, archive_session)).archive(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
    )
    assert archived.status == "archived"

    restore_session = FakeSession([archived])
    restored = await ResearchWorkspaceService(cast(AsyncSession, restore_session)).restore(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
    )
    assert restored.status == "active"


@pytest.mark.asyncio
async def test_get_owned_returns_not_found_for_foreign_or_deleted_resources() -> None:
    """查询不到资源时统一错误，路由层会映射为不泄漏信息的 404。"""
    session = FakeSession([None])

    with pytest.raises(WorkspaceError) as error:
        await ResearchWorkspaceService(cast(AsyncSession, session)).get_owned(
            owner_user_id=_OWNER_ID,
            collection_id=uuid4(),
        )

    assert error.value.code is WorkspaceErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_list_owned_uses_an_opaque_cursor_for_the_next_page() -> None:
    """列表多取一条判断下一页，并用最后一条已返回记录生成游标。"""
    now = datetime.now(UTC)
    first = _listed_collection(collection_id=UUID(int=301), updated_at=now)
    second = _listed_collection(collection_id=UUID(int=302), updated_at=now - timedelta(minutes=1))
    extra = _listed_collection(collection_id=UUID(int=303), updated_at=now - timedelta(minutes=2))
    first_session = FakeSession(scalar_batches=[[first, second, extra]])

    first_page = await ResearchWorkspaceService(cast(AsyncSession, first_session)).list_owned(
        owner_user_id=_OWNER_ID,
        query="研究中",
        limit=2,
    )

    assert first_page.items == [first, second]
    assert first_page.next_cursor is not None

    final = _listed_collection(collection_id=UUID(int=304), updated_at=now - timedelta(minutes=3))
    second_session = FakeSession(scalar_batches=[[final]])
    second_page = await ResearchWorkspaceService(cast(AsyncSession, second_session)).list_owned(
        owner_user_id=_OWNER_ID,
        cursor=first_page.next_cursor,
        limit=2,
    )

    assert second_page.items == [final]
    assert second_page.next_cursor is None


@pytest.mark.asyncio
async def test_list_owned_rejects_a_damaged_cursor() -> None:
    """客户端传回损坏游标时返回稳定错误，而不是暴露解码异常。"""
    service = ResearchWorkspaceService(cast(AsyncSession, FakeSession()))

    with pytest.raises(WorkspaceError) as error:
        await service.list_owned(
            owner_user_id=_OWNER_ID,
            cursor="not-a-workspace-cursor",
        )

    assert error.value.code is WorkspaceErrorCode.INVALID_CURSOR
