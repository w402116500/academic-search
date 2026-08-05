"""搜索候选全文获取与短期状态查询路由。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.deps.auth import get_current_user
from app.api.deps.services import (
    get_candidate_fulltext_service,
    get_candidate_upload_service,
    get_collection_admission_service,
)
from app.modules.auth.models import UserAccount
from app.modules.documents.api_contracts import (
    CandidateFulltextError,
    CandidateFulltextErrorCode,
    CandidateFulltextResponse,
)
from app.modules.documents.service import (
    CandidateFulltextService,
    CandidateFulltextSubmission,
)
from app.modules.literature.admission import (
    CollectionAdmissionError,
    CollectionAdmissionErrorCode,
    CollectionAdmissionResult,
    LiteratureAdmissionCandidate,
    LiteratureAdmissionPort,
)
from app.modules.search.api_contracts import (
    SearchRunError,
    SearchRunErrorCode,
)

router = APIRouter(prefix="/collections", tags=["全文获取"])


def _fulltext_error_response(error: CandidateFulltextError) -> HTTPException:
    """将全文候选领域错误映射为前端可区分的 HTTP 状态。"""
    if error.code in {
        CandidateFulltextErrorCode.CANDIDATE_NOT_FOUND,
        CandidateFulltextErrorCode.STATE_NOT_FOUND,
    }:
        status_code = status.HTTP_404_NOT_FOUND
    elif error.code is CandidateFulltextErrorCode.SESSION_EXPIRED:
        status_code = status.HTTP_410_GONE
    elif error.code is CandidateFulltextErrorCode.UPLOAD_NOT_AUTHORIZED:
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code, detail={"code": error.code, "message": str(error)}
    )


def _search_run_error_response(error: SearchRunError) -> HTTPException:
    """将全文流程依赖的检索运行错误转换为稳定 HTTP 响应。"""
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


def _admission_error_response(error: CollectionAdmissionError) -> HTTPException:
    """将严格准入服务的错误映射为前端可恢复的 HTTP 响应。"""
    if error.code is CollectionAdmissionErrorCode.COLLECTION_UNAVAILABLE:
        status_code = status.HTTP_404_NOT_FOUND
    elif error.code is CollectionAdmissionErrorCode.STORAGE_ERROR:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error), "retryable": error.retryable},
    )


def _response(submission: CandidateFulltextSubmission) -> CandidateFulltextResponse:
    """不暴露 Redis 键和 arq Job ID，仅返回前端需要的状态事实。"""
    state = submission.state
    result = state.result
    return CandidateFulltextResponse(
        search_run_id=state.search_run_id,
        candidate_id=state.candidate.candidate_id,
        attempt_no=state.attempt_no,
        status=result.status,
        document=result.document,
        error=result.error,
        requested_at=state.requested_at,
        updated_at=state.updated_at,
    )


@router.post(
    "/{collection_id}/search-runs/{search_run_id}/candidates/{candidate_id}/fulltext",
    response_model=CandidateFulltextResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="获取候选的开放获取全文",
)
async def request_candidate_fulltext(
    collection_id: UUID,
    search_run_id: UUID,
    candidate_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CandidateFulltextService, Depends(get_candidate_fulltext_service)],
) -> CandidateFulltextResponse:
    """创建全文异步任务；候选、URL 和题录均从服务端检索会话读取。"""
    try:
        submission = await service.request(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
        )
    except CandidateFulltextError as exc:
        raise _fulltext_error_response(exc) from exc
    except SearchRunError as exc:
        raise _search_run_error_response(exc) from exc
    return _response(submission)


@router.post(
    "/{collection_id}/search-runs/{search_run_id}/candidates/{candidate_id}/fulltext/retry",
    response_model=CandidateFulltextResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="重试候选全文获取",
)
async def retry_candidate_fulltext(
    collection_id: UUID,
    search_run_id: UUID,
    candidate_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CandidateFulltextService, Depends(get_candidate_fulltext_service)],
) -> CandidateFulltextResponse:
    """仅为可重试的终态失败创建新的全文下载尝试。"""
    try:
        submission = await service.request(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            retry=True,
        )
    except CandidateFulltextError as exc:
        raise _fulltext_error_response(exc) from exc
    except SearchRunError as exc:
        raise _search_run_error_response(exc) from exc
    return _response(submission)


@router.post(
    "/{collection_id}/search-runs/{search_run_id}/candidates/{candidate_id}/fulltext/upload",
    response_model=CandidateFulltextResponse,
    summary="上传有权处理的候选 PDF",
)
async def upload_candidate_fulltext(
    collection_id: UUID,
    search_run_id: UUID,
    candidate_id: UUID,
    request: Request,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CandidateFulltextService, Depends(get_candidate_upload_service)],
    x_upload_authorized: Annotated[str | None, Header()] = None,
) -> CandidateFulltextResponse:
    """只接收二进制 PDF 流；授权、候选、DOI 和暂存对象键都由服务端控制。"""
    try:
        submission = await service.upload(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            authorized_to_process=x_upload_authorized == "true",
            chunks=request.stream(),
            media_type=request.headers.get("content-type"),
        )
    except CandidateFulltextError as exc:
        raise _fulltext_error_response(exc) from exc
    except SearchRunError as exc:
        raise _search_run_error_response(exc) from exc
    return _response(submission)


@router.get(
    "/{collection_id}/search-runs/{search_run_id}/candidates/{candidate_id}/fulltext",
    response_model=CandidateFulltextResponse,
    summary="获取候选全文任务状态",
)
async def get_candidate_fulltext(
    collection_id: UUID,
    search_run_id: UUID,
    candidate_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CandidateFulltextService, Depends(get_candidate_fulltext_service)],
) -> CandidateFulltextResponse:
    """供前端轮询正在下载、校验或已可加入集合的候选全文。"""
    try:
        submission = await service.get_state(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
        )
    except CandidateFulltextError as exc:
        raise _fulltext_error_response(exc) from exc
    except SearchRunError as exc:
        raise _search_run_error_response(exc) from exc
    return _response(submission)


@router.post(
    "/{collection_id}/search-runs/{search_run_id}/candidates/{candidate_id}/fulltext/admission",
    response_model=CollectionAdmissionResult,
    status_code=status.HTTP_201_CREATED,
    summary="将已验证全文加入待确认集合",
)
async def admit_candidate_fulltext(
    collection_id: UUID,
    search_run_id: UUID,
    candidate_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CandidateFulltextService, Depends(get_candidate_fulltext_service)],
    admission_service: Annotated[
        LiteratureAdmissionPort, Depends(get_collection_admission_service)
    ],
) -> CollectionAdmissionResult:
    """只接受当前搜索会话中已校验 PDF，创建不会被 Worker 提前领取的 pending 运行。"""
    owner_user_id = current_user.id
    try:
        submission = await service.get_state(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
        )
        return await admission_service.admit(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            candidate=LiteratureAdmissionCandidate(
                candidate_id=submission.state.candidate.candidate_id,
                doi=submission.state.candidate.doi,
                abstract=submission.state.candidate.abstract,
                official_url=(
                    submission.state.candidate.links.landing_url
                    or submission.state.candidate.links.open_access_url
                ),
                citation=submission.state.candidate.citation,
            ),
            fulltext_result=submission.state.result,
        )
    except CandidateFulltextError as exc:
        raise _fulltext_error_response(exc) from exc
    except SearchRunError as exc:
        raise _search_run_error_response(exc) from exc
    except CollectionAdmissionError as exc:
        raise _admission_error_response(exc) from exc
