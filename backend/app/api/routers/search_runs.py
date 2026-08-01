"""多源检索运行、候选快照和进度事件路由。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from app.api.deps.auth import get_current_user
from app.core.settings import get_literature_source_settings
from app.db.models.user import User
from app.db.session import get_db_session
from app.modules.workflow.contracts import (
    SearchCandidatesResponse,
    SearchProgressEvent,
    SearchRunError,
    SearchRunErrorCode,
    SearchRunResponse,
)
from app.modules.workflow.job_queue import ArqSearchRunJobQueue
from app.modules.workflow.search_run_service import SearchRunService
from app.modules.workflow.search_session import SearchSessionStore
from app.modules.workflow.state import SearchRunStage, SearchRunStatus
from app.workers.redis import redis_client_from_environment
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

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
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SearchRunResponse:
    """仅使用已确认研究计划创建一次可恢复的检索运行。"""
    try:
        submission = await SearchRunService(
            session,
            ArqSearchRunJobQueue(),
        ).start_search(
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
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SearchRunResponse:
    """刷新页面后恢复工作区最近一次检索运行状态。"""
    try:
        run = await SearchRunService(session).get_current_run(
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
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SearchRunResponse:
    """读取当前用户工作区内的一次检索运行。"""
    try:
        run = await SearchRunService(session).get_owned_run(
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
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SearchRunResponse:
    """为失败、部分失败或过期运行创建新的尝试，不覆盖历史运行。"""
    try:
        submission = await SearchRunService(
            session,
            ArqSearchRunJobQueue(),
        ).retry_search(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            previous_run_id=search_run_id,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc
    return SearchRunResponse.model_validate(submission.search_run)


@router.get(
    "/{collection_id}/search-runs/{search_run_id}/candidates",
    response_model=SearchCandidatesResponse,
    summary="获取检索候选文献",
)
async def get_search_candidates(
    collection_id: UUID,
    search_run_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SearchCandidatesResponse:
    """从当前用户拥有的检索运行 Redis 会话读取候选详情。"""
    try:
        run = await SearchRunService(session).get_owned_run(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
    except SearchRunError as exc:
        raise _search_error_response(exc) from exc

    if run.redis_session_key is None:
        raise _search_error_response(
            SearchRunError(SearchRunErrorCode.SESSION_EXPIRED, "检索候选会话不存在。")
        )

    settings = get_literature_source_settings()
    redis = redis_client_from_environment()
    try:
        snapshot = await SearchSessionStore(
            redis,
            ttl_seconds=settings.search_session_ttl_seconds,
        ).read_snapshot(run.redis_session_key)
    finally:
        await redis.aclose()

    if snapshot is None:
        await SearchRunService(session).expire_run(run.id)
        raise _search_error_response(
            SearchRunError(
                SearchRunErrorCode.SESSION_EXPIRED,
                "检索候选已过期，请重新执行文献检索。",
            )
        )

    return SearchCandidatesResponse(
        run_id=run.id,
        status=SearchRunStatus(snapshot.get("status", run.status)),
        candidate_counts=snapshot.get("candidate_counts", {}),
        candidates=snapshot.get("candidates", []),
    )


@router.get(
    "/{collection_id}/search-runs/{search_run_id}/events",
    summary="订阅检索进度事件",
)
async def stream_search_events(
    request: Request,
    collection_id: UUID,
    search_run_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StreamingResponse:
    """使用 Redis Stream 向前端推送可恢复的检索阶段和来源状态。"""
    try:
        run = await SearchRunService(session).get_owned_run(
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

    settings = get_literature_source_settings()
    redis = redis_client_from_environment()
    store = SearchSessionStore(redis, ttl_seconds=settings.search_session_ttl_seconds)
    last_event_id = request.headers.get("Last-Event-ID", "$")

    async def event_stream() -> AsyncIterator[str]:
        """先发送数据库状态，再等待 Redis Stream 的后续事件。"""
        cursor = last_event_id
        try:
            initial_event = SearchProgressEvent(
                run_id=run.id,
                status=SearchRunStatus(run.status),
                stage=SearchRunStage(run.stage),
                provider_summary=run.provider_summary,
                candidate_counts=run.candidate_counts,
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
                    current_run = await SearchRunService(session).get_owned_run(
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
        finally:
            await redis.aclose()

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
