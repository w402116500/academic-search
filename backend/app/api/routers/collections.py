"""研究工作区的创建与生命周期管理路由。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from app.api.deps.auth import get_current_user
from app.db.models.user import User
from app.db.session import get_db_session
from app.modules.collections.workspace_contracts import (
    CreateWorkspaceRequest,
    UpdateWorkspaceRequest,
    WorkspaceError,
    WorkspaceErrorCode,
    WorkspaceListResponse,
    WorkspaceResponse,
)
from app.modules.collections.workspace_service import ResearchWorkspaceService
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/collections", tags=["研究工作区"])


def _workspace_error_response(error: WorkspaceError) -> HTTPException:
    """工作区越权和不存在统一返回 404，不向用户泄漏资源归属。"""
    status_code_by_error = {
        WorkspaceErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
        WorkspaceErrorCode.NOT_ACTIVE: status.HTTP_409_CONFLICT,
        WorkspaceErrorCode.INVALID_CURSOR: status.HTTP_422_UNPROCESSABLE_CONTENT,
    }
    return HTTPException(
        status_code=status_code_by_error[error.code],
        detail={"code": error.code, "message": str(error)},
    )


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建研究工作区",
)
async def create_workspace(
    request: CreateWorkspaceRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WorkspaceResponse:
    """创建一个空工作区；研究问题将在进入工作区后另行确认。"""
    collection = await ResearchWorkspaceService(session).create(
        owner_user_id=current_user.id,
        request=request,
    )
    return WorkspaceResponse.model_validate(collection)


@router.get("", response_model=WorkspaceListResponse, summary="搜索我的研究工作区")
async def list_workspaces(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    include_archived: bool = Query(default=False, description="是否包含已归档工作区"),
    query: str | None = Query(
        default=None,
        alias="q",
        min_length=1,
        max_length=200,
        description="按工作区名称或当前研究阶段搜索",
    ),
    cursor: str | None = Query(
        default=None,
        max_length=1_000,
        description="上一页返回的不透明分页游标",
    ),
    limit: int = Query(default=20, ge=1, le=50, description="本次最多返回的工作区数量"),
) -> WorkspaceListResponse:
    """供工作区切换器按需加载，默认只显示活动工作区。"""
    try:
        page = await ResearchWorkspaceService(session).list_owned(
            owner_user_id=current_user.id,
            include_archived=include_archived,
            query=query,
            cursor=cursor,
            limit=limit,
        )
    except WorkspaceError as exc:
        raise _workspace_error_response(exc) from exc
    return WorkspaceListResponse(
        items=[WorkspaceResponse.model_validate(collection) for collection in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/{collection_id}", response_model=WorkspaceResponse, summary="获取研究工作区详情")
async def get_workspace(
    collection_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WorkspaceResponse:
    """读取当前用户拥有的单个活动或归档工作区。"""
    try:
        collection = await ResearchWorkspaceService(session).get_owned(
            owner_user_id=current_user.id,
            collection_id=collection_id,
        )
    except WorkspaceError as exc:
        raise _workspace_error_response(exc) from exc
    return WorkspaceResponse.model_validate(collection)


@router.patch("/{collection_id}", response_model=WorkspaceResponse, summary="修改活动工作区信息")
async def update_workspace(
    collection_id: UUID,
    request: UpdateWorkspaceRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WorkspaceResponse:
    """仅更新名称和说明，归档工作区必须先恢复才能编辑。"""
    try:
        collection = await ResearchWorkspaceService(session).update(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            request=request,
        )
    except WorkspaceError as exc:
        raise _workspace_error_response(exc) from exc
    return WorkspaceResponse.model_validate(collection)


@router.post("/{collection_id}/archive", response_model=WorkspaceResponse, summary="归档研究工作区")
async def archive_workspace(
    collection_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WorkspaceResponse:
    """归档后停止新的文献写入与研究活动，已保存数据保持可恢复。"""
    try:
        collection = await ResearchWorkspaceService(session).archive(
            owner_user_id=current_user.id,
            collection_id=collection_id,
        )
    except WorkspaceError as exc:
        raise _workspace_error_response(exc) from exc
    return WorkspaceResponse.model_validate(collection)


@router.post("/{collection_id}/restore", response_model=WorkspaceResponse, summary="恢复研究工作区")
async def restore_workspace(
    collection_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WorkspaceResponse:
    """恢复后工作区可继续接收后续检索和文献入库任务。"""
    try:
        collection = await ResearchWorkspaceService(session).restore(
            owner_user_id=current_user.id,
            collection_id=collection_id,
        )
    except WorkspaceError as exc:
        raise _workspace_error_response(exc) from exc
    return WorkspaceResponse.model_validate(collection)
