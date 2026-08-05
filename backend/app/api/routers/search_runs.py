"""多源检索运行、候选快照和进度事件路由。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps.auth import get_current_user
from app.api.deps.services import (
    get_candidate_review_admission_service,
    get_candidate_review_prepare_service,
    get_candidate_review_query_service,
    get_candidate_selection_service,
    get_search_run_service,
    get_search_session_store,
)
from app.modules.auth.models import UserAccount
from app.modules.search.api_contracts import (
    CandidateAdmissionBatchResponse,
    CandidateCounts,
    CandidatePreparationBatchResponse,
    CandidateReviewFilter,
    CandidateSelectionRequest,
    CandidateSelectionResponse,
    SearchCandidatePageResponse,
    SearchCandidateReviewItem,
    SearchProgressEvent,
    SearchRunError,
    SearchRunErrorCode,
    SearchRunResponse,
)
from app.modules.search.review_admission import CandidateAdmissionService
from app.modules.search.review_preparation import CandidatePreparationService
from app.modules.search.review_query import CandidateReviewQueryService
from app.modules.search.review_selection import CandidateSelectionService
from app.modules.search.review_session import (
    CandidateReviewError,
    CandidateReviewErrorCode,
)
from app.modules.search.run_service import SearchRunService
from app.modules.search.session import SearchSessionStore
from app.modules.search.state import SearchRunStage, SearchRunStatus

router = APIRouter(prefix="/collections", tags=["文献检索"])


def _search_error_response(error: SearchRunError) -> HTTPException:
    """将检索领域错误映射为稳定 HTTP 响应。"""
    if error.code in {
        SearchRunErrorCode.COLLECTION_NOT_FOUND,
        SearchRunErrorCode.RUN_NOT_FOUND,
    }:
        status_code = status.HTTP_404_NOT_FOUND
    elif error.code is SearchRunErrorCode.SESSION_EXPIRED:
        status_code = status.HTTP_410_GONE
    elif error.code is SearchRunErrorCode.QUEUE_UNAVAILABLE:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif error.code in {
        SearchRunErrorCode.USER_QUOTA_EXCEEDED,
        SearchRunErrorCode.GLOBAL_BUDGET_EXHAUSTED,
    }:
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
    else:
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
    )


def _candidate_review_error_response(error: CandidateReviewError) -> HTTPException:
    """将候选审核会话、选择和游标错误映射为前端可恢复的 HTTP 状态。"""
    if error.code is CandidateReviewErrorCode.CANDIDATE_NOT_FOUND:
        status_code = status.HTTP_404_NOT_FOUND
    elif error.code is CandidateReviewErrorCode.SESSION_EXPIRED:
        status_code = status.HTTP_410_GONE
    elif error.code is CandidateReviewErrorCode.INVALID_CURSOR:
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
    )


@router.post(
    "/{collection_id}/search-runs",
    response_model=SearchRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="开始多源文献检索",
)
async def start_search_run(
    collection_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[SearchRunService, Depends(get_search_run_service)],
) -> SearchRunResponse:
    """仅使用已确认研究计划创建一次可恢复的检索运行。"""
    try:
        submission = await service.start_search(
            owner_user_id=current_user.id,
            collection_id=collection_id,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc
    return SearchRunResponse.model_validate(submission.search_run)


@router.get(
    "/{collection_id}/search-runs/current",
    response_model=SearchRunResponse,
    summary="获取当前检索运行",
)
async def get_current_search_run(
    collection_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[SearchRunService, Depends(get_search_run_service)],
) -> SearchRunResponse:
    """刷新页面后恢复工作区最近一次检索运行状态。"""
    try:
        run = await service.get_current_run(
            owner_user_id=current_user.id,
            collection_id=collection_id,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc
    return SearchRunResponse.model_validate(run)


@router.get(
    "/{collection_id}/search-runs/{search_run_id}",
    response_model=SearchRunResponse,
    summary="获取检索运行详情",
)
async def get_search_run(
    collection_id: UUID,
    search_run_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[SearchRunService, Depends(get_search_run_service)],
) -> SearchRunResponse:
    """读取当前用户工作区内的一次检索运行。"""
    try:
        run = await service.get_owned_run(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc
    return SearchRunResponse.model_validate(run)


@router.post(
    "/{collection_id}/search-runs/{search_run_id}/retry",
    response_model=SearchRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="重试失败的文献检索",
)
async def retry_search_run(
    collection_id: UUID,
    search_run_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[SearchRunService, Depends(get_search_run_service)],
) -> SearchRunResponse:
    """为失败、部分失败或过期运行创建新的尝试，不覆盖历史运行。"""
    try:
        submission = await service.retry_search(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            previous_run_id=search_run_id,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc
    return SearchRunResponse.model_validate(submission.search_run)


@router.get(
    "/{collection_id}/search-runs/{search_run_id}/candidates",
    response_model=SearchCandidatePageResponse,
    summary="获取检索候选文献",
)
async def get_search_candidates(
    collection_id: UUID,
    search_run_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CandidateReviewQueryService, Depends(get_candidate_review_query_service)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    query: Annotated[str, Query(max_length=200)] = "",
    review_filter: Annotated[
        CandidateReviewFilter, Query(alias="filter")
    ] = CandidateReviewFilter.ALL,
) -> SearchCandidatePageResponse:
    """服务端分页读取候选，并把跨页准备清单与全文状态一并返回。"""
    try:
        return await service.page(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            limit=limit,
            cursor=cursor,
            query=query,
            review_filter=review_filter,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc
    except CandidateReviewError as exc:
        raise _candidate_review_error_response(exc) from exc


@router.get(
    "/{collection_id}/search-runs/{search_run_id}/candidates/{candidate_id}",
    response_model=SearchCandidateReviewItem,
    summary="获取单篇候选审核详情",
)
async def get_search_candidate(
    collection_id: UUID,
    search_run_id: UUID,
    candidate_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CandidateReviewQueryService, Depends(get_candidate_review_query_service)],
) -> SearchCandidateReviewItem:
    """按候选 ID 返回详情所需状态，避免详情页受候选分页位置影响。"""
    try:
        return await service.item(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc
    except CandidateReviewError as exc:
        raise _candidate_review_error_response(exc) from exc


@router.patch(
    "/{collection_id}/search-runs/{search_run_id}/candidate-selection",
    response_model=CandidateSelectionResponse,
    summary="更新本次候选准备清单",
)
async def update_candidate_selection(
    collection_id: UUID,
    search_run_id: UUID,
    payload: CandidateSelectionRequest,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CandidateSelectionService, Depends(get_candidate_selection_service)],
) -> CandidateSelectionResponse:
    """选择只保存候选 UUID；正文、DOI 与题录都继续从 Redis 会话读取。"""
    try:
        return await service.update_selection(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            candidate_ids=payload.candidate_ids,
            selected=payload.selected,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc
    except CandidateReviewError as exc:
        raise _candidate_review_error_response(exc) from exc


@router.delete(
    "/{collection_id}/search-runs/{search_run_id}/candidate-selection",
    response_model=CandidateSelectionResponse,
    summary="清空本次候选准备清单",
)
async def clear_candidate_selection(
    collection_id: UUID,
    search_run_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CandidateSelectionService, Depends(get_candidate_selection_service)],
) -> CandidateSelectionResponse:
    """只清除 Redis 中本次准备选择，已加入待确认集合的文献不会受影响。"""
    try:
        return await service.clear_selection(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc
    except CandidateReviewError as exc:
        raise _candidate_review_error_response(exc) from exc


@router.post(
    "/{collection_id}/search-runs/{search_run_id}/candidate-selection/prepare",
    response_model=CandidatePreparationBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="批量准备题录与全文核验",
)
async def prepare_candidate_selection(
    collection_id: UUID,
    search_run_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CandidatePreparationService, Depends(get_candidate_review_prepare_service)],
) -> CandidatePreparationBatchResponse:
    """把准备清单逐篇投递到既有全文 Worker，不等待下载结果才返回 HTTP。"""
    try:
        return await service.prepare_selected(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc
    except CandidateReviewError as exc:
        raise _candidate_review_error_response(exc) from exc


@router.post(
    "/{collection_id}/search-runs/{search_run_id}/candidate-selection/admission",
    response_model=CandidateAdmissionBatchResponse,
    summary="批量加入待确认集合",
)
async def admit_candidate_selection(
    collection_id: UUID,
    search_run_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[CandidateAdmissionService, Depends(get_candidate_review_admission_service)],
) -> CandidateAdmissionBatchResponse:
    """仅把全文已可处理的候选逐篇加入待确认集合，失败项留在准备清单。"""
    try:
        return await service.admit_selected(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc
    except CandidateReviewError as exc:
        raise _candidate_review_error_response(exc) from exc


@router.get(
    "/{collection_id}/search-runs/{search_run_id}/events",
    responses={
        status.HTTP_200_OK: {
            "model": SearchProgressEvent,
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/SearchProgressEvent"}
                }
            },
        }
    },
    summary="订阅检索进度事件",
)
async def stream_search_events(
    request: Request,
    collection_id: UUID,
    search_run_id: UUID,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    service: Annotated[SearchRunService, Depends(get_search_run_service)],
    store: Annotated[SearchSessionStore, Depends(get_search_session_store)],
) -> StreamingResponse:
    """使用 Redis Stream 向前端推送可恢复的检索阶段和来源状态。"""
    try:
        run = await service.get_owned_run(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc

    if run.redis_session_key is None:
        raise _search_error_response(
            SearchRunError(SearchRunErrorCode.SESSION_EXPIRED, "检索进度会话不存在。")
        )
    session_key = run.redis_session_key

    last_event_id = request.headers.get("Last-Event-ID", "$")

    async def event_stream() -> AsyncIterator[str]:
        """先发送数据库状态，再等待 Redis Stream 的后续事件。"""
        cursor = last_event_id
        initial_event = SearchProgressEvent(
            run_id=run.id,
            status=SearchRunStatus(run.status),
            stage=SearchRunStage(run.stage),
            provider_summary=run.provider_summary,
            candidate_counts=cast(CandidateCounts, run.candidate_counts),
            message="已连接检索进度流。",
        )
        yield _sse_message("snapshot", "initial", initial_event.model_dump(mode="json"))
        while True:
            # 终态运行在建立连接时已经由初始快照完整表达，不再阻塞等待新事件。
            # 这也覆盖 Redis Stream 已过期、但 PostgreSQL 仍保留终态审计记录的情况。
            if run.status in {
                SearchRunStatus.COMPLETED.value,
                SearchRunStatus.PARTIAL_FAILED.value,
                SearchRunStatus.FAILED.value,
                SearchRunStatus.EXPIRED.value,
                SearchRunStatus.CANCELLED.value,
            }:
                break
            events = await store.read_events(
                session_key,
                last_event_id=cursor,
            )
            if events:
                for event_id, event in events:
                    cursor = event_id
                    yield _sse_message("progress", event_id, event)
                if any(
                    event.get("status")
                    in {
                        SearchRunStatus.COMPLETED.value,
                        SearchRunStatus.PARTIAL_FAILED.value,
                        SearchRunStatus.FAILED.value,
                    }
                    for _event_id, event in events
                ):
                    break
            else:
                yield ": keep-alive\n\n"
                current_run = await service.get_owned_run(
                    owner_user_id=current_user.id,
                    collection_id=collection_id,
                    search_run_id=search_run_id,
                )
                if current_run.status in {
                    SearchRunStatus.COMPLETED.value,
                    SearchRunStatus.PARTIAL_FAILED.value,
                    SearchRunStatus.FAILED.value,
                    SearchRunStatus.EXPIRED.value,
                    SearchRunStatus.CANCELLED.value,
                }:
                    break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_message(event_name: str, event_id: str, payload: dict[str, object]) -> str:
    """将结构化进度编码为浏览器 EventSource 可读取的 SSE 消息。"""
    return (
        f"id: {event_id}\nevent: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    )
