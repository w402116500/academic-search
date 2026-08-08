"""研究工作区的所有权隔离和生命周期操作。"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.modules.research.state import WorkspaceWorkflowStage, get_workflow_stage_presentation
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
    WorkspaceRepository,
)


@dataclass(frozen=True, slots=True)
class WorkspacePage:
    """服务层返回的工作区游标分页结果。"""

    items: list[ResearchWorkspace]
    next_cursor: str | None


class ResearchWorkspaceService:
    """只操作当前用户拥有的研究工作区。

    工作区的 ``status`` 表示生命周期，不表示检索或入库进度；后者由未来的
    搜索会话和 ``ingestion_runs`` 负责，避免混淆权限边界与任务状态。
    """

    def __init__(self, workspaces: WorkspaceRepository) -> None:
        self._workspaces = workspaces

    async def create(
        self, *, owner_user_id: UUID, request: CreateWorkspaceRequest
    ) -> ResearchWorkspace:
        """为当前用户创建一个空的活动研究工作区。"""
        return await self._workspaces.create(
            CreateResearchWorkspace(
                owner_user_id=owner_user_id,
                name=request.name,
                description=request.description,
            )
        )

    async def list_owned(
        self,
        *,
        owner_user_id: UUID,
        include_archived: bool = False,
        query: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> WorkspacePage:
        """按最近更新顺序搜索并分页返回当前用户的可用或待完成删除工作区。"""
        statuses = (
            ("active", "archived", "deleting") if include_archived else ("active", "deleting")
        )
        normalized_query = " ".join(query.split()) if query else None
        cursor_updated_at: datetime | None = None
        cursor_id: UUID | None = None
        if cursor:
            cursor_updated_at, cursor_id = _decode_workspace_cursor(cursor)
        collections = await self._workspaces.list_owned(
            WorkspaceListFilter(
                owner_user_id=owner_user_id,
                statuses=statuses,
                query=normalized_query,
                matching_workflow_stages=(
                    _matching_workflow_stages(normalized_query) if normalized_query else ()
                ),
                before_updated_at=cursor_updated_at,
                before_id=cursor_id,
                limit=limit + 1,
            )
        )
        has_more = len(collections) > limit
        items = collections[:limit]
        next_cursor = _encode_workspace_cursor(items[-1]) if has_more and items else None
        return WorkspacePage(items=items, next_cursor=next_cursor)

    async def get_owned(self, *, owner_user_id: UUID, collection_id: UUID) -> ResearchWorkspace:
        """读取当前用户拥有的一个非删除工作区，不泄漏其他用户的资源存在性。"""
        collection = await self._workspaces.get_owned(
            owner_user_id=owner_user_id, workspace_id=collection_id
        )
        if collection is None:
            raise WorkspaceError(WorkspaceErrorCode.NOT_FOUND, "研究工作区不存在。")
        return collection

    async def update(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        request: UpdateWorkspaceRequest,
    ) -> ResearchWorkspace:
        """只允许修改活动工作区的名称和说明。"""
        collection = await self.get_owned(owner_user_id=owner_user_id, collection_id=collection_id)
        self._require_active(collection)
        return await self._workspaces.update_details(
            owner_user_id=owner_user_id,
            workspace_id=collection_id,
            changes=UpdateWorkspaceDetails(
                name=request.name,
                description=request.description,
                change_description="description" in request.model_fields_set,
            ),
        )

    async def archive(self, *, owner_user_id: UUID, collection_id: UUID) -> ResearchWorkspace:
        """归档工作区；重复归档保持幂等，避免刷新页面造成错误。"""
        collection = await self.get_owned(owner_user_id=owner_user_id, collection_id=collection_id)
        if collection.status == "active":
            collection = await self._workspaces.set_status(
                owner_user_id=owner_user_id,
                workspace_id=collection_id,
                status="archived",
            )
        return collection

    async def restore(self, *, owner_user_id: UUID, collection_id: UUID) -> ResearchWorkspace:
        """恢复归档工作区；物理删除不在首版 API 范围内。"""
        collection = await self.get_owned(owner_user_id=owner_user_id, collection_id=collection_id)
        if collection.status == "archived":
            collection = await self._workspaces.set_status(
                owner_user_id=owner_user_id,
                workspace_id=collection_id,
                status="active",
            )
        return collection

    @staticmethod
    def _require_active(collection: ResearchWorkspace) -> None:
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


def _encode_workspace_cursor(collection: ResearchWorkspace) -> str:
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
