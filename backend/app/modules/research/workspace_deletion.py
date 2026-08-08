"""研究工作区永久删除的业务边界与编排。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.modules.research.workspace_contracts import WorkspaceError, WorkspaceErrorCode

logger = logging.getLogger(__name__)

_DELETION_INCOMPLETE_MESSAGE = "工作区删除尚未完成，请稍后继续删除。"


@dataclass(frozen=True, slots=True)
class WorkspaceDeletionSnapshot:
    """开始删除时固定的工作区私有外部资源清单。"""

    ingestion_run_ids: tuple[UUID, ...]
    document_object_keys: tuple[str, ...]


class WorkspaceDeletionRepository(Protocol):
    """永久删除所需的持久化状态闸门与清理快照。"""

    async def begin_deletion(
        self, *, owner_user_id: UUID, workspace_id: UUID
    ) -> WorkspaceDeletionSnapshot | None: ...

    async def has_running_ingestion(self, *, workspace_id: UUID) -> bool: ...

    async def has_running_research(self, *, workspace_id: UUID) -> bool: ...

    async def delete_root(self, *, owner_user_id: UUID, workspace_id: UUID) -> bool: ...


class WorkspaceDeletionObjectStorage(Protocol):
    """删除工作区私有全文对象所需的最小端口。"""

    async def delete_object(self, *, object_key: str) -> None: ...


class WorkspaceDeletionVectorIndex(Protocol):
    """删除工作区私有向量所需的最小端口。"""

    async def delete_ingestion_run(self, ingestion_run_id: UUID) -> None: ...


class ResearchWorkspaceDeletionService:
    """在物理删除前收敛异步运行，并清理工作区私有外部资源。"""

    def __init__(
        self,
        repository: WorkspaceDeletionRepository,
        storage: WorkspaceDeletionObjectStorage,
        vector_index: WorkspaceDeletionVectorIndex,
        *,
        wait_timeout_seconds: float = 30,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._vector_index = vector_index
        self._wait_timeout_seconds = wait_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    async def delete(self, *, owner_user_id: UUID, collection_id: UUID) -> None:
        """永久删除一个工作区；外部资源失败时保留 ``deleting`` 状态以便重试。"""
        try:
            snapshot = await self._repository.begin_deletion(
                owner_user_id=owner_user_id,
                workspace_id=collection_id,
            )
            if snapshot is None:
                raise WorkspaceError(WorkspaceErrorCode.NOT_FOUND, "研究工作区不存在。")

            await self._wait_for_running_work(collection_id)
            await self._delete_vectors(collection_id, snapshot)
            await self._delete_document_objects(collection_id, snapshot)

            # 并发的同一删除请求可能已完成根记录删除；开始时已确认所有权，因此可视为成功。
            await self._repository.delete_root(
                owner_user_id=owner_user_id,
                workspace_id=collection_id,
            )
        except WorkspaceError:
            raise
        except Exception as exc:
            logger.exception("工作区删除的数据库清理失败 workspace_id=%s", collection_id)
            raise WorkspaceError(
                WorkspaceErrorCode.DELETION_CLEANUP_FAILED,
                _DELETION_INCOMPLETE_MESSAGE,
            ) from exc

    async def _wait_for_running_work(self, collection_id: UUID) -> None:
        """等待协作取消到达安全边界，避免入库在向量清理后迟到写入。"""
        deadline = asyncio.get_running_loop().time() + self._wait_timeout_seconds
        while True:
            # 仓储由同一个请求级 AsyncSession 实现，不能以 gather 并发使用该会话。
            has_running_ingestion = await self._repository.has_running_ingestion(
                workspace_id=collection_id
            )
            has_running_research = await self._repository.has_running_research(
                workspace_id=collection_id
            )
            if not has_running_ingestion and not has_running_research:
                return
            if asyncio.get_running_loop().time() >= deadline:
                logger.warning("工作区删除等待后台任务停止超时 workspace_id=%s", collection_id)
                raise WorkspaceError(
                    WorkspaceErrorCode.DELETION_IN_PROGRESS,
                    _DELETION_INCOMPLETE_MESSAGE,
                )
            await asyncio.sleep(self._poll_interval_seconds)

    async def _delete_vectors(
        self, collection_id: UUID, snapshot: WorkspaceDeletionSnapshot
    ) -> None:
        """按入库运行精确删除向量；任一失败都不能继续删除数据库根记录。"""
        try:
            for ingestion_run_id in snapshot.ingestion_run_ids:
                await self._vector_index.delete_ingestion_run(ingestion_run_id)
        except Exception as exc:
            logger.exception("工作区删除的向量清理失败 workspace_id=%s", collection_id)
            raise WorkspaceError(
                WorkspaceErrorCode.DELETION_CLEANUP_FAILED,
                _DELETION_INCOMPLETE_MESSAGE,
            ) from exc

    async def _delete_document_objects(
        self, collection_id: UUID, snapshot: WorkspaceDeletionSnapshot
    ) -> None:
        """删除已准入的私有全文对象；对象存储删除本身支持幂等重试。"""
        try:
            for object_key in snapshot.document_object_keys:
                await self._storage.delete_object(object_key=object_key)
        except Exception as exc:
            logger.exception("工作区删除的全文对象清理失败 workspace_id=%s", collection_id)
            raise WorkspaceError(
                WorkspaceErrorCode.DELETION_CLEANUP_FAILED,
                _DELETION_INCOMPLETE_MESSAGE,
            ) from exc
