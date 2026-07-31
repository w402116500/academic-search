"""研究工作区的所有权隔离和生命周期操作。"""

from __future__ import annotations

from uuid import UUID

from app.db.models.collection import ResearchCollection
from app.modules.collections.workspace_contracts import (
    CreateWorkspaceRequest,
    UpdateWorkspaceRequest,
    WorkspaceError,
    WorkspaceErrorCode,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class ResearchWorkspaceService:
    """只操作当前用户拥有的研究工作区。

    工作区的 ``status`` 表示生命周期，不表示检索或入库进度；后者由未来的
    搜索会话和 ``ingestion_runs`` 负责，避免混淆权限边界与任务状态。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, owner_user_id: UUID, request: CreateWorkspaceRequest
    ) -> ResearchCollection:
        """为当前用户创建一个空的活动研究工作区。"""
        collection = ResearchCollection(
            owner_user_id=owner_user_id,
            name=request.name,
            description=request.description,
            status="active",
        )
        self._session.add(collection)
        # 路由鉴权会先在同一会话读取用户，不能在这里重新 begin；直接提交当前
        # 请求事务可同时固化用户读取后的工作区写入。
        await self._session.commit()
        await self._session.refresh(collection)
        return collection

    async def list_owned(
        self,
        *,
        owner_user_id: UUID,
        include_archived: bool = False,
    ) -> list[ResearchCollection]:
        """按最近更新排序返回当前用户的非删除工作区。"""
        statuses = ("active", "archived") if include_archived else ("active",)
        statement = (
            select(ResearchCollection)
            .where(
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status.in_(statuses),
            )
            .order_by(ResearchCollection.updated_at.desc(), ResearchCollection.id.desc())
        )
        result = await self._session.scalars(statement)
        return list(result)

    async def get_owned(self, *, owner_user_id: UUID, collection_id: UUID) -> ResearchCollection:
        """读取当前用户拥有的一个非删除工作区，不泄漏其他用户的资源存在性。"""
        statement = select(ResearchCollection).where(
            ResearchCollection.id == collection_id,
            ResearchCollection.owner_user_id == owner_user_id,
            ResearchCollection.status.in_(("active", "archived")),
        )
        collection = await self._session.scalar(statement)
        if collection is None:
            raise WorkspaceError(WorkspaceErrorCode.NOT_FOUND, "研究工作区不存在。")
        return collection

    async def update(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        request: UpdateWorkspaceRequest,
    ) -> ResearchCollection:
        """只允许修改活动工作区的名称和说明。"""
        collection = await self.get_owned(owner_user_id=owner_user_id, collection_id=collection_id)
        self._require_active(collection)
        if request.name is not None:
            collection.name = request.name
        # PATCH 已校验至少有一个字段，因此 None 代表调用方没有修改说明。
        if "description" in request.model_fields_set:
            collection.description = request.description
        await self._session.commit()
        await self._session.refresh(collection)
        return collection

    async def archive(self, *, owner_user_id: UUID, collection_id: UUID) -> ResearchCollection:
        """归档工作区；重复归档保持幂等，避免刷新页面造成错误。"""
        collection = await self.get_owned(owner_user_id=owner_user_id, collection_id=collection_id)
        if collection.status == "active":
            collection.status = "archived"
            await self._session.commit()
            await self._session.refresh(collection)
        return collection

    async def restore(self, *, owner_user_id: UUID, collection_id: UUID) -> ResearchCollection:
        """恢复归档工作区；物理删除不在首版 API 范围内。"""
        collection = await self.get_owned(owner_user_id=owner_user_id, collection_id=collection_id)
        if collection.status == "archived":
            collection.status = "active"
            await self._session.commit()
            await self._session.refresh(collection)
        return collection

    @staticmethod
    def _require_active(collection: ResearchCollection) -> None:
        """阻止归档工作区被普通写操作修改。"""
        if collection.status != "active":
            raise WorkspaceError(WorkspaceErrorCode.NOT_ACTIVE, "研究工作区已归档。")
