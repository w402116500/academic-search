"""研究集合内待构建文献和入库运行的 API 路由。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps.auth import get_current_user
from app.api.deps.services import get_collection_build_service
from app.modules.auth.models import UserAccount
from app.modules.research.build_contracts import (
    CollectionBuildError,
    CollectionBuildErrorCode,
    CollectionBuildResponse,
    CollectionDocumentRemovalResponse,
    CollectionDocumentsResponse,
)
from app.modules.research.collection_build import CollectionBuildUseCases

router = APIRouter(prefix="/collections", tags=["研究集合构建"])


def _build_error_response(error: CollectionBuildError) -> HTTPException:
    """集合构建错误统一映射为不泄漏其他用户资源的 HTTP 响应。"""
    if error.code in {
        CollectionBuildErrorCode.COLLECTION_NOT_FOUND,
        CollectionBuildErrorCode.DOCUMENT_NOT_FOUND,
    }:
        status_code = status.HTTP_404_NOT_FOUND
    elif error.code in {
        CollectionBuildErrorCode.USER_QUOTA_EXCEEDED,
        CollectionBuildErrorCode.GLOBAL_BUDGET_EXHAUSTED,
    }:
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
    else:
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
    )


@router.get(
    "/{collection_id}/documents",
    response_model=CollectionDocumentsResponse,
    summary="获取集合文献及入库状态",
)
async def list_collection_documents(
    collection_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CollectionBuildUseCases, Depends(get_collection_build_service)],
) -> CollectionDocumentsResponse:
    """页面刷新时读取活动文献、失败原因与可以进入研究对话的文献数量。"""
    try:
        return await service.list_documents(
            owner_user_id=current_user.id,
            collection_id=collection_id,
        )
    except CollectionBuildError as exc:
        raise _build_error_response(exc) from exc


@router.post(
    "/{collection_id}/build",
    response_model=CollectionBuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="确认并构建研究集合",
)
async def build_collection(
    collection_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CollectionBuildUseCases, Depends(get_collection_build_service)],
) -> CollectionBuildResponse:
    """将所有待确认文献投递到 RAG 入库 Worker；每篇文献独立报告投递结果。"""
    try:
        return await service.build(
            owner_user_id=current_user.id,
            collection_id=collection_id,
        )
    except CollectionBuildError as exc:
        raise _build_error_response(exc) from exc


@router.post(
    "/{collection_id}/ingestion-runs/{ingestion_run_id}/retry",
    response_model=CollectionBuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="重试失败的文献入库",
)
async def retry_ingestion_run(
    collection_id: UUID,
    ingestion_run_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CollectionBuildUseCases, Depends(get_collection_build_service)],
) -> CollectionBuildResponse:
    """新建入库运行而非覆盖失败记录，使错误和重试序号始终可审计。"""
    try:
        return await service.retry_run(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            ingestion_run_id=ingestion_run_id,
        )
    except CollectionBuildError as exc:
        raise _build_error_response(exc) from exc


@router.delete(
    "/{collection_id}/documents/{document_id}",
    response_model=CollectionDocumentRemovalResponse,
    summary="从待确认集合移出文献",
)
async def remove_pending_document(
    collection_id: UUID,
    document_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CollectionBuildUseCases, Depends(get_collection_build_service)],
) -> CollectionDocumentRemovalResponse:
    """仅归档 pending 文献，保留正式对象和审计记录以避免跨服务删除不一致。"""
    try:
        return await service.remove_pending_document(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            document_id=document_id,
        )
    except CollectionBuildError as exc:
        raise _build_error_response(exc) from exc
