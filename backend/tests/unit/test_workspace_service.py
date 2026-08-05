"""研究工作区所有权和生命周期的离线服务测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.modules.research.workspace_contracts import (
    CreateWorkspaceRequest,
    UpdateWorkspaceRequest,
    WorkspaceError,
    WorkspaceErrorCode,
)
from app.modules.research.workspace_models import ResearchWorkspace
from app.modules.research.workspace_repository import (
    CreateResearchWorkspace,
    UpdateWorkspaceDetails,
    WorkspaceListFilter,
)
from app.modules.research.workspace_service import ResearchWorkspaceService

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000201")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000202")


class FakeWorkspaceRepository:
    """Workspace persistence port replacement for lifecycle tests."""

    def __init__(
        self,
        workspaces: list[ResearchWorkspace] | None = None,
        list_batches: list[list[ResearchWorkspace]] | None = None,
    ) -> None:
        self.workspaces = {workspace.id: workspace for workspace in workspaces or []}
        self._list_batches = iter(list_batches or [])
        self.created_commands: list[CreateResearchWorkspace] = []
        self.detail_updates: list[UpdateWorkspaceDetails] = []

    async def create(self, command: CreateResearchWorkspace) -> ResearchWorkspace:
        self.created_commands.append(command)
        workspace = ResearchWorkspace(
            id=_COLLECTION_ID,
            owner_user_id=command.owner_user_id,
            name=command.name,
            description=command.description,
            research_question=None,
            status="active",
            workflow_stage="draft",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.workspaces[workspace.id] = workspace
        return workspace

    async def list_owned(self, query: WorkspaceListFilter) -> list[ResearchWorkspace]:
        return next(self._list_batches)

    async def get_owned(
        self, *, owner_user_id: UUID, workspace_id: UUID
    ) -> ResearchWorkspace | None:
        workspace = self.workspaces.get(workspace_id)
        if workspace is None or workspace.owner_user_id != owner_user_id:
            return None
        return workspace

    async def update_details(
        self,
        *,
        owner_user_id: UUID,
        workspace_id: UUID,
        changes: UpdateWorkspaceDetails,
    ) -> ResearchWorkspace:
        workspace = self.workspaces[workspace_id]
        assert workspace.owner_user_id == owner_user_id
        self.detail_updates.append(changes)
        updated = replace(
            workspace,
            name=changes.name if changes.name is not None else workspace.name,
            description=(
                changes.description if changes.change_description else workspace.description
            ),
        )
        self.workspaces[workspace_id] = updated
        return updated

    async def set_status(
        self, *, owner_user_id: UUID, workspace_id: UUID, status: str
    ) -> ResearchWorkspace:
        workspace = self.workspaces[workspace_id]
        assert workspace.owner_user_id == owner_user_id
        updated = replace(workspace, status=status)
        self.workspaces[workspace_id] = updated
        return updated


def _collection(*, status: str = "active") -> ResearchWorkspace:
    """构建一个已属于测试用户的工作区。"""
    now = datetime.now(UTC)
    return ResearchWorkspace(
        id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        name="Original name",
        description="Original description",
        research_question=None,
        status=status,
        workflow_stage="draft",
        created_at=now,
        updated_at=now,
    )


def _listed_collection(*, collection_id: UUID, updated_at: datetime) -> ResearchWorkspace:
    """构建具有稳定分页排序键的工作区。"""
    return replace(_collection(), id=collection_id, updated_at=updated_at)


@pytest.mark.asyncio
async def test_create_assigns_the_owner_from_the_service_boundary() -> None:
    """客户端请求中没有 owner 字段，服务只接受认证依赖传入的用户标识。"""
    workspaces = FakeWorkspaceRepository()
    service = ResearchWorkspaceService(workspaces)

    created = await service.create(
        owner_user_id=_OWNER_ID,
        request=CreateWorkspaceRequest(name="  Green   space\nresearch  ", description="  Notes  "),
    )

    assert len(workspaces.created_commands) == 1
    assert created.owner_user_id == _OWNER_ID
    assert created.name == "Green space research"
    assert created.description == "Notes"
    assert created.status == "active"


@pytest.mark.asyncio
async def test_update_can_clear_description_but_rejects_archived_workspace() -> None:
    """显式 null 用于清空说明，归档后的普通编辑必须失败。"""
    active = _collection()
    workspaces = FakeWorkspaceRepository([active])
    service = ResearchWorkspaceService(workspaces)

    updated = await service.update(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        request=UpdateWorkspaceRequest(description=None),
    )

    assert updated.description is None
    assert len(workspaces.detail_updates) == 1

    archived_workspaces = FakeWorkspaceRepository([_collection(status="archived")])
    with pytest.raises(WorkspaceError) as error:
        await ResearchWorkspaceService(archived_workspaces).update(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            request=UpdateWorkspaceRequest(name="Renamed"),
        )
    assert error.value.code is WorkspaceErrorCode.NOT_ACTIVE


@pytest.mark.asyncio
async def test_archive_and_restore_are_idempotent() -> None:
    """重复调用生命周期操作不应使前端重试变成错误。"""
    active = _collection()
    archive_workspaces = FakeWorkspaceRepository([active])
    archived = await ResearchWorkspaceService(archive_workspaces).archive(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
    )
    assert archived.status == "archived"

    restore_workspaces = FakeWorkspaceRepository([archived])
    restored = await ResearchWorkspaceService(restore_workspaces).restore(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
    )
    assert restored.status == "active"


@pytest.mark.asyncio
async def test_get_owned_returns_not_found_for_foreign_or_deleted_resources() -> None:
    """查询不到资源时统一错误，路由层会映射为不泄漏信息的 404。"""
    workspaces = FakeWorkspaceRepository()

    with pytest.raises(WorkspaceError) as error:
        await ResearchWorkspaceService(workspaces).get_owned(
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
    first_workspaces = FakeWorkspaceRepository(list_batches=[[first, second, extra]])

    first_page = await ResearchWorkspaceService(first_workspaces).list_owned(
        owner_user_id=_OWNER_ID,
        query="研究中",
        limit=2,
    )

    assert first_page.items == [first, second]
    assert first_page.next_cursor is not None

    final = _listed_collection(collection_id=UUID(int=304), updated_at=now - timedelta(minutes=3))
    second_workspaces = FakeWorkspaceRepository(list_batches=[[final]])
    second_page = await ResearchWorkspaceService(second_workspaces).list_owned(
        owner_user_id=_OWNER_ID,
        cursor=first_page.next_cursor,
        limit=2,
    )

    assert second_page.items == [final]
    assert second_page.next_cursor is None


@pytest.mark.asyncio
async def test_list_owned_rejects_a_damaged_cursor() -> None:
    """客户端传回损坏游标时返回稳定错误，而不是暴露解码异常。"""
    service = ResearchWorkspaceService(FakeWorkspaceRepository())

    with pytest.raises(WorkspaceError) as error:
        await service.list_owned(
            owner_user_id=_OWNER_ID,
            cursor="not-a-workspace-cursor",
        )

    assert error.value.code is WorkspaceErrorCode.INVALID_CURSOR
