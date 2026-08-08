"""工作区永久删除的离线服务测试。"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from app.modules.research.workspace_contracts import WorkspaceError, WorkspaceErrorCode
from app.modules.research.workspace_deletion import (
    ResearchWorkspaceDeletionService,
    WorkspaceDeletionSnapshot,
)

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000701")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000702")
_RUN_ONE_ID = UUID("00000000-0000-0000-0000-000000000703")
_RUN_TWO_ID = UUID("00000000-0000-0000-0000-000000000704")


class FakeWorkspaceDeletionRepository:
    """记录删除围栏与根记录删除的最小持久化替身。"""

    def __init__(self, *, running_ingestion: bool = False, running_research: bool = False) -> None:
        self.snapshot = WorkspaceDeletionSnapshot(
            ingestion_run_ids=(_RUN_ONE_ID, _RUN_TWO_ID),
            document_object_keys=("documents/one.pdf", "documents/two.pdf"),
        )
        self.running_ingestion = running_ingestion
        self.running_research = running_research
        self.begin_calls = 0
        self.deleted_root = False
        self.running_checks: list[str] = []
        self._check_in_progress = False

    async def begin_deletion(
        self, *, owner_user_id: UUID, workspace_id: UUID
    ) -> WorkspaceDeletionSnapshot | None:
        assert owner_user_id == _OWNER_ID
        assert workspace_id == _COLLECTION_ID
        self.begin_calls += 1
        return self.snapshot

    async def has_running_ingestion(self, *, workspace_id: UUID) -> bool:
        assert workspace_id == _COLLECTION_ID
        await self._record_running_check("ingestion")
        return self.running_ingestion

    async def has_running_research(self, *, workspace_id: UUID) -> bool:
        assert workspace_id == _COLLECTION_ID
        await self._record_running_check("research")
        return self.running_research

    async def delete_root(self, *, owner_user_id: UUID, workspace_id: UUID) -> bool:
        assert owner_user_id == _OWNER_ID
        assert workspace_id == _COLLECTION_ID
        self.deleted_root = True
        return True

    async def _record_running_check(self, kind: str) -> None:
        """模拟共享 AsyncSession，确保服务不会并发执行两个状态查询。"""
        assert not self._check_in_progress, "同一个删除仓储不应并发执行状态查询"
        self._check_in_progress = True
        self.running_checks.append(kind)
        await asyncio.sleep(0)
        self._check_in_progress = False


class FakeVectorIndex:
    """记录精确运行范围的向量删除。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.deleted_run_ids: list[UUID] = []
        self._fail = fail

    async def delete_ingestion_run(self, ingestion_run_id: UUID) -> None:
        self.deleted_run_ids.append(ingestion_run_id)
        if self._fail:
            raise RuntimeError("vector delete failed")


class FakeStorage:
    """记录私有对象删除，不访问真实对象存储。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.deleted_object_keys: list[str] = []
        self._fail = fail

    async def delete_object(self, *, object_key: str) -> None:
        self.deleted_object_keys.append(object_key)
        if self._fail:
            raise RuntimeError("object delete failed")


@pytest.mark.asyncio
async def test_delete_cleans_all_private_resources_before_deleting_root() -> None:
    """先完成 Milvus 和对象存储清理，最后才允许数据库级联删除工作区。"""
    repository = FakeWorkspaceDeletionRepository()
    vector_index = FakeVectorIndex()
    storage = FakeStorage()
    service = ResearchWorkspaceDeletionService(repository, storage, vector_index)

    await service.delete(owner_user_id=_OWNER_ID, collection_id=_COLLECTION_ID)

    assert repository.begin_calls == 1
    assert repository.running_checks == ["ingestion", "research"]
    assert vector_index.deleted_run_ids == [_RUN_ONE_ID, _RUN_TWO_ID]
    assert storage.deleted_object_keys == ["documents/one.pdf", "documents/two.pdf"]
    assert repository.deleted_root is True


@pytest.mark.asyncio
async def test_delete_keeps_workspace_fenced_when_a_running_task_does_not_stop() -> None:
    """超时不能伪造成功，保留 deleting 状态供稍后以同一 DELETE 重试。"""
    repository = FakeWorkspaceDeletionRepository(running_ingestion=True)
    vector_index = FakeVectorIndex()
    storage = FakeStorage()
    service = ResearchWorkspaceDeletionService(
        repository,
        storage,
        vector_index,
        wait_timeout_seconds=0,
        poll_interval_seconds=0,
    )

    with pytest.raises(WorkspaceError) as raised:
        await service.delete(owner_user_id=_OWNER_ID, collection_id=_COLLECTION_ID)

    assert raised.value.code is WorkspaceErrorCode.DELETION_IN_PROGRESS
    assert not vector_index.deleted_run_ids
    assert not storage.deleted_object_keys
    assert repository.deleted_root is False


@pytest.mark.asyncio
async def test_delete_keeps_root_when_external_cleanup_fails() -> None:
    """外部资源任一项失败时不能物理删除记录，后续重试仍有完整清理清单。"""
    repository = FakeWorkspaceDeletionRepository()
    vector_index = FakeVectorIndex(fail=True)
    storage = FakeStorage()
    service = ResearchWorkspaceDeletionService(repository, storage, vector_index)

    with pytest.raises(WorkspaceError) as raised:
        await service.delete(owner_user_id=_OWNER_ID, collection_id=_COLLECTION_ID)

    assert raised.value.code is WorkspaceErrorCode.DELETION_CLEANUP_FAILED
    assert vector_index.deleted_run_ids == [_RUN_ONE_ID]
    assert not storage.deleted_object_keys
    assert repository.deleted_root is False
