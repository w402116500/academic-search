"""研究工作区的所有权隔离和生命周期操作。"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.models.collection import ResearchCollection
from app.modules.collections.workspace_contracts import (
    CreateWorkspaceRequest,
    UpdateWorkspaceRequest,
    WorkspaceError,
    WorkspaceErrorCode,
)
from app.modules.workflow.state import WorkspaceWorkflowStage, get_workflow_stage_presentation
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class WorkspacePage:
    """服务层返回的工作区游标分页结果。"""

    items: list[ResearchCollection]
    next_cursor: str | None


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
        query: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> WorkspacePage:
        """按最近更新顺序搜索并分页返回当前用户的非删除工作区。"""
        statuses = ("active", "archived") if include_archived else ("active",)
        filters = [
            ResearchCollection.owner_user_id == owner_user_id,
            ResearchCollection.status.in_(statuses),
        ]

        normalized_query = " ".join(query.split()) if query else None
        if normalized_query:
            # 阶段搜索同时理解稳定英文值和 API 展示的中文标签/说明，前端无需维护映射。
            matching_stages = _matching_workflow_stages(normalized_query)
            name_pattern = f"%{_escape_like_pattern(normalized_query)}%"
            search_filters = [
                ResearchCollection.name.ilike(name_pattern, escape="\\"),
            ]
            if matching_stages:
                search_filters.append(ResearchCollection.workflow_stage.in_(matching_stages))
            filters.append(or_(*search_filters))

        if cursor:
            cursor_updated_at, cursor_id = _decode_workspace_cursor(cursor)
            # 使用更新时间和 UUID 组成稳定排序键，避免同一秒更新的工作区重复或漏页。
            filters.append(
                or_(
                    ResearchCollection.updated_at < cursor_updated_at,
                    and_(
                        ResearchCollection.updated_at == cursor_updated_at,
                        ResearchCollection.id < cursor_id,
                    ),
                )
            )

        statement = (
            select(ResearchCollection)
            .where(*filters)
            .order_by(ResearchCollection.updated_at.desc(), ResearchCollection.id.desc())
            # 多取一条只用于判断是否还有下一页，不把额外记录暴露给客户端。
            .limit(limit + 1)
        )
        result = await self._session.scalars(statement)
        collections = list(result)
        has_more = len(collections) > limit
        items = collections[:limit]
        next_cursor = _encode_workspace_cursor(items[-1]) if has_more and items else None
        return WorkspacePage(items=items, next_cursor=next_cursor)

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


def _matching_workflow_stages(query: str) -> tuple[str, ...]:
    """将用户输入匹配到工作流阶段的英文值或中文展示文本。"""
    normalized_query = query.casefold()
    matches: list[str] = []
    for stage in WorkspaceWorkflowStage:
        presentation = get_workflow_stage_presentation(stage)
        searchable_text = " ".join(
            (stage.value, presentation.label, presentation.description)
        ).casefold()
        if normalized_query in searchable_text:
            matches.append(stage.value)
    return tuple(matches)


def _escape_like_pattern(value: str) -> str:
    """转义 SQL LIKE 通配符，让搜索框中的百分号和下划线按普通字符处理。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _encode_workspace_cursor(collection: ResearchCollection) -> str:
    """把最后一条记录的稳定排序键编码为客户端不可解释的游标。"""
    payload = json.dumps(
        {"updated_at": collection.updated_at.isoformat(), "id": str(collection.id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_workspace_cursor(cursor: str) -> tuple[datetime, UUID]:
    """校验并解码工作区游标，任何损坏内容都转换为稳定业务错误。"""
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        updated_at = datetime.fromisoformat(payload["updated_at"])
        collection_id = UUID(payload["id"])
        if updated_at.tzinfo is None:
            raise ValueError("游标时间缺少时区")
    except (
        binascii.Error,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise WorkspaceError(
            WorkspaceErrorCode.INVALID_CURSOR,
            "工作区分页游标无效，请重新加载列表。",
        ) from exc
    return updated_at, collection_id
