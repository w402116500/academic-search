"""SQLAlchemy implementation for durable workspace deletion fencing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.collection import ResearchCollection
from app.infra.db.models.document import Document, IngestionRun
from app.infra.db.models.research import ResearchEvidence, ResearchRun
from app.infra.db.models.workflow import ResearchPlan, SearchRun
from app.modules.rag.ingestion.contracts import IngestionErrorCode
from app.modules.research.contracts import ResearchRunStage, ResearchRunStatus
from app.modules.research.state import ResearchPlanStatus
from app.modules.research.workspace_deletion import WorkspaceDeletionSnapshot
from app.modules.search.state import SearchRunStage, SearchRunStatus

_T = TypeVar("_T")


class SqlAlchemyWorkspaceDeletionRepository:
    """以 ``deleting`` 状态围栏工作区，并提供可重试的清理快照。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def begin_deletion(
        self, *, owner_user_id: UUID, workspace_id: UUID
    ) -> WorkspaceDeletionSnapshot | None:
        """原子阻断新写入、请求运行停止，并读取本次外部清理清单。"""
        return await self._run_write_transaction(
            lambda: self._begin_deletion(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
        )

    async def has_running_ingestion(self, *, workspace_id: UUID) -> bool:
        """只有运行中的入库会阻挡根记录删除，排队任务已在开始删除时终态取消。"""
        run_id = await self._session.scalar(
            select(IngestionRun.id)
            .join(Document, Document.id == IngestionRun.document_id)
            .where(
                Document.collection_id == workspace_id,
                IngestionRun.status == "running",
            )
            .limit(1)
        )
        await self._session.rollback()
        return run_id is not None

    async def has_running_research(self, *, workspace_id: UUID) -> bool:
        """研究运行通过现有持久取消标记在图节点安全边界收敛。"""
        run_id = await self._session.scalar(
            select(ResearchRun.id)
            .where(
                ResearchRun.collection_id == workspace_id,
                ResearchRun.status == ResearchRunStatus.RUNNING.value,
            )
            .limit(1)
        )
        await self._session.rollback()
        return run_id is not None

    async def delete_root(self, *, owner_user_id: UUID, workspace_id: UUID) -> bool:
        """按审计外键要求清理私有记录，再删除已经完成外部清理的根记录。"""
        return await self._run_write_transaction(
            lambda: self._delete_root(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
        )

    async def _run_write_transaction(self, operation: Callable[[], Awaitable[_T]]) -> _T:
        if self._session.in_transaction():
            try:
                result = await operation()
            except Exception:
                await self._session.rollback()
                raise
            await self._session.commit()
            return result

        async with self._session.begin():
            return await operation()

    async def _begin_deletion(
        self, *, owner_user_id: UUID, workspace_id: UUID
    ) -> WorkspaceDeletionSnapshot | None:
        collection = await self._session.scalar(
            select(ResearchCollection)
            .where(
                ResearchCollection.id == workspace_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status.in_(("active", "archived", "deleting")),
            )
            .with_for_update()
        )
        if collection is None:
            return None

        now = datetime.now(UTC)
        collection.status = "deleting"
        await self._cancel_background_work(workspace_id=workspace_id, now=now)

        ingestion_run_ids = tuple(
            await self._session.scalars(
                select(IngestionRun.id)
                .join(Document, Document.id == IngestionRun.document_id)
                .where(Document.collection_id == workspace_id)
                .order_by(IngestionRun.id)
            )
        )
        document_object_keys = tuple(
            await self._session.scalars(
                select(Document.object_key)
                .where(Document.collection_id == workspace_id)
                .order_by(Document.id)
            )
        )
        return WorkspaceDeletionSnapshot(
            ingestion_run_ids=ingestion_run_ids,
            document_object_keys=document_object_keys,
        )

    async def _delete_root(self, *, owner_user_id: UUID, workspace_id: UUID) -> bool:
        collection = await self._session.scalar(
            select(ResearchCollection)
            .where(
                ResearchCollection.id == workspace_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status == "deleting",
            )
            .with_for_update()
        )
        if collection is None:
            return False

        research_run_ids = select(ResearchRun.id).where(ResearchRun.collection_id == workspace_id)
        await self._session.execute(
            delete(ResearchEvidence).where(ResearchEvidence.research_run_id.in_(research_run_ids))
        )
        await self._session.execute(
            delete(ResearchRun).where(ResearchRun.collection_id == workspace_id)
        )
        await self._session.delete(collection)
        return True

    async def _cancel_background_work(self, *, workspace_id: UUID, now: datetime) -> None:
        """持久化取消事实，不依赖 HTTP 进程或 Redis 队列仍然存活。"""
        document_ids = select(Document.id).where(Document.collection_id == workspace_id)

        await self._session.execute(
            update(ResearchPlan)
            .where(
                ResearchPlan.collection_id == workspace_id,
                ResearchPlan.status == ResearchPlanStatus.GENERATING.value,
            )
            .values(
                status=ResearchPlanStatus.FAILED.value,
                error_code="workspace_deleting",
                error_message="研究工作区正在删除，已停止计划分析。",
            )
        )
        await self._session.execute(
            update(SearchRun)
            .where(
                SearchRun.collection_id == workspace_id,
                SearchRun.status.in_((SearchRunStatus.QUEUED.value, SearchRunStatus.RUNNING.value)),
            )
            .values(
                status=SearchRunStatus.CANCELLED.value,
                stage=SearchRunStage.COMPLETED.value,
                error_code="workspace_deleting",
                error_message="研究工作区正在删除，已停止文献检索。",
                finished_at=now,
            )
        )
        await self._session.execute(
            update(ResearchRun)
            .where(
                ResearchRun.collection_id == workspace_id,
                ResearchRun.status == ResearchRunStatus.QUEUED.value,
            )
            .values(
                status=ResearchRunStatus.CANCELLED.value,
                stage=ResearchRunStage.CANCELLED.value,
                finished_at=now,
            )
        )
        await self._session.execute(
            update(ResearchRun)
            .where(
                ResearchRun.collection_id == workspace_id,
                ResearchRun.status == ResearchRunStatus.RUNNING.value,
                ResearchRun.cancel_requested_at.is_(None),
            )
            .values(cancel_requested_at=now)
        )
        await self._session.execute(
            update(IngestionRun)
            .where(
                IngestionRun.document_id.in_(document_ids),
                IngestionRun.status.in_(("pending", "queued")),
            )
            .values(
                status="cancelled",
                is_current=False,
                error_code=IngestionErrorCode.CANCELLED.value,
                error_message="研究工作区正在删除，已停止文献入库。",
                finished_at=now,
            )
        )
        await self._session.execute(
            update(IngestionRun)
            .where(
                IngestionRun.document_id.in_(document_ids),
                IngestionRun.status == "running",
                IngestionRun.cancel_requested_at.is_(None),
            )
            .values(cancel_requested_at=now)
        )
