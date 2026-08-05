"""当前检索候选的正式引用格式化路由。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps.auth import get_current_user
from app.api.deps.services import get_candidate_citation_service
from app.modules.auth.models import UserAccount
from app.modules.literature.api_contracts import (
    CandidateCitationError,
    CandidateCitationErrorCode,
    CandidateCitationResponse,
)
from app.modules.literature.citation_formatter import CitationFormat
from app.modules.search.api_contracts import (
    SearchRunError,
    SearchRunErrorCode,
)
from app.modules.search.citation_service import CandidateCitationService

router = APIRouter(prefix="/collections", tags=["正式引用"])


def _citation_error_response(error: CandidateCitationError) -> HTTPException:
    """将候选题录错误转换为前端可恢复且不泄漏其他用户资源的信息。"""
    if error.code is CandidateCitationErrorCode.CANDIDATE_NOT_FOUND:
        status_code = status.HTTP_404_NOT_FOUND
    elif error.code is CandidateCitationErrorCode.SESSION_EXPIRED:
        status_code = status.HTTP_410_GONE
    else:
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
    )


def _search_run_error_response(error: SearchRunError) -> HTTPException:
    """保持候选路由与检索运行路由一致的所有权和基础设施错误语义。"""
    if error.code in {
        SearchRunErrorCode.COLLECTION_NOT_FOUND,
        SearchRunErrorCode.RUN_NOT_FOUND,
    }:
        status_code = status.HTTP_404_NOT_FOUND
    elif error.code is SearchRunErrorCode.QUEUE_UNAVAILABLE:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
    )


@router.get(
    "/{collection_id}/search-runs/{search_run_id}/candidates/{candidate_id}/citation",
    response_model=CandidateCitationResponse,
    summary="生成候选的正式引用",
)
async def render_candidate_citation(
    collection_id: UUID,
    search_run_id: UUID,
    candidate_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CandidateCitationService, Depends(get_candidate_citation_service)],
    citation_format: Annotated[
        CitationFormat,
        Query(alias="format", description="需要渲染的正式引用格式"),
    ] = CitationFormat.GB_T_7714_2015_NUMERIC,
) -> CandidateCitationResponse:
    """从 Redis 候选快照的 `ready` 格式中立题录渲染一种可复制引用。"""
    try:
        rendered = await service.render(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            citation_format=citation_format,
        )
    except CandidateCitationError as exc:
        raise _citation_error_response(exc) from exc
    except SearchRunError as exc:
        raise _search_run_error_response(exc) from exc
    return CandidateCitationResponse(
        candidate_id=rendered.candidate_id,
        format=rendered.format,
        text=rendered.text,
    )
