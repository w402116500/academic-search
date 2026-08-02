"""研究集合内会话和异步研究运行的 FastAPI 路由。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from app.api.deps.auth import get_current_user
from app.db.models.user import User
from app.db.session import get_db_session
from app.modules.research.contracts import (
    AskResearchQuestionRequest,
    AskResearchQuestionResponse,
    ConversationDetailResponse,
    ConversationResponse,
    CreateConversationRequest,
    ResearchError,
    ResearchErrorCode,
    ResearchProgressEvent,
    ResearchRunResponse,
    ResearchRunStatus,
)
from app.modules.research.events import ResearchEventStore
from app.modules.research.job_queue import ArqResearchJobQueue
from app.modules.research.service import ResearchConversationService
from app.modules.research.settings import get_research_settings
from app.modules.workflow.settings import get_workflow_settings
from app.workers.redis import redis_client_from_environment
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/collections", tags=["证据研究会话"])


def _research_error_response(error: ResearchError) -> HTTPException:
    """按错误类别返回不泄漏其他用户资源的统一 HTTP 语义。"""
    status_code = (
        status.HTTP_404_NOT_FOUND
        if error.code
        in {
            ResearchErrorCode.COLLECTION_NOT_FOUND,
            ResearchErrorCode.CONVERSATION_NOT_FOUND,
            ResearchErrorCode.RUN_NOT_FOUND,
        }
        else status.HTTP_503_SERVICE_UNAVAILABLE
        if error.code is ResearchErrorCode.QUEUE_UNAVAILABLE
        else status.HTTP_409_CONFLICT
    )
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
    )


@router.get(
    "/{collection_id}/conversations",
    response_model=list[ConversationResponse],
    summary="获取研究会话列表",
)
async def list_conversations(
    collection_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ConversationResponse]:
    """只返回当前用户在该研究集合中的非删除会话。"""
    try:
        return await ResearchConversationService(session).list_conversations(
            owner_user_id=current_user.id, collection_id=collection_id
        )
    except ResearchError as exc:
        raise _research_error_response(exc) from exc


@router.post(
    "/{collection_id}/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建研究会话",
)
async def create_conversation(
    collection_id: UUID,
    request: CreateConversationRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConversationResponse:
    """集合至少有一篇已完成索引文献时才允许创建可提问会话。"""
    try:
        return await ResearchConversationService(session).create_conversation(
            owner_user_id=current_user.id, collection_id=collection_id, request=request
        )
    except ResearchError as exc:
        raise _research_error_response(exc) from exc


@router.get(
    "/{collection_id}/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="获取研究会话详情",
)
async def get_conversation(
    collection_id: UUID,
    conversation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConversationDetailResponse:
    """刷新页面时从 PostgreSQL 恢复消息和研究运行，而非依赖浏览器内存。"""
    try:
        return await ResearchConversationService(session).get_conversation(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            conversation_id=conversation_id,
        )
    except ResearchError as exc:
        raise _research_error_response(exc) from exc


@router.post(
    "/{collection_id}/conversations/{conversation_id}/questions",
    response_model=AskResearchQuestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="提交研究问题",
)
async def ask_research_question(
    collection_id: UUID,
    conversation_id: UUID,
    request: AskResearchQuestionRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AskResearchQuestionResponse:
    """问题和 queued 运行先落库，再由独立 Worker 完成 RAG 处理。"""
    try:
        return await ResearchConversationService(session, ArqResearchJobQueue()).ask_question(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            conversation_id=conversation_id,
            content=request.content,
            model_config=get_workflow_settings().model_snapshot,
        )
    except ResearchError as exc:
        raise _research_error_response(exc) from exc


@router.get(
    "/{collection_id}/conversations/{conversation_id}/research-runs/{research_run_id}",
    response_model=ResearchRunResponse,
    summary="获取研究运行状态",
)
async def get_research_run(
    collection_id: UUID,
    conversation_id: UUID,
    research_run_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResearchRunResponse:
    """轮询或 SSE 断线恢复时读取持久化运行快照。"""
    try:
        return await ResearchConversationService(session).get_run(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            conversation_id=conversation_id,
            research_run_id=research_run_id,
        )
    except ResearchError as exc:
        raise _research_error_response(exc) from exc


@router.get(
    "/{collection_id}/conversations/{conversation_id}/research-runs/{research_run_id}/events",
    summary="订阅研究回答进度",
)
async def stream_research_events(
    request: Request,
    collection_id: UUID,
    conversation_id: UUID,
    research_run_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StreamingResponse:
    """以 Redis Stream 推送公开阶段；重连时仍以 PostgreSQL 当前状态为起点。"""
    service = ResearchConversationService(session)
    try:
        initial = await service.get_run(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            conversation_id=conversation_id,
            research_run_id=research_run_id,
        )
    except ResearchError as exc:
        raise _research_error_response(exc) from exc

    redis = redis_client_from_environment()
    store = ResearchEventStore(redis, ttl_seconds=get_research_settings().rag_event_ttl_seconds)
    last_event_id = request.headers.get("Last-Event-ID", "$")

    async def event_stream() -> AsyncIterator[str]:
        """终态只发送数据库快照；运行中才阻塞等待下一条 Redis 事件。"""
        cursor = last_event_id
        current_run = initial
        try:
            snapshot = ResearchProgressEvent(
                run_id=current_run.id,
                status=current_run.status,
                stage=current_run.stage,
                message="已连接研究进度流。",
                evidence_count=len(current_run.evidences),
            )
            yield _sse_message("snapshot", "initial", snapshot.model_dump(mode="json"))
            while current_run.status not in {
                ResearchRunStatus.COMPLETED,
                ResearchRunStatus.AWAITING_CLARIFICATION,
                ResearchRunStatus.FAILED,
                ResearchRunStatus.CANCELLED,
            }:
                events = await store.read_events(research_run_id, last_event_id=cursor)
                if events:
                    for event_id, event in events:
                        cursor = event_id
                        yield _sse_message("progress", event_id, event)
                        if event.get("status") in {
                            ResearchRunStatus.COMPLETED.value,
                            ResearchRunStatus.AWAITING_CLARIFICATION.value,
                            ResearchRunStatus.FAILED.value,
                            ResearchRunStatus.CANCELLED.value,
                        }:
                            return
                else:
                    yield ": keep-alive\n\n"
                    current_run = await service.get_run(
                        owner_user_id=current_user.id,
                        collection_id=collection_id,
                        conversation_id=conversation_id,
                        research_run_id=research_run_id,
                    )
        finally:
            await redis.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/{collection_id}/conversations/{conversation_id}/research-runs/{research_run_id}/retry",
    response_model=ResearchRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="重新投递失败研究运行",
)
async def retry_research_run(
    collection_id: UUID,
    conversation_id: UUID,
    research_run_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResearchRunResponse:
    """重试复用同一业务运行，审计和 checkpoint 标识保持可追踪。"""
    try:
        return await ResearchConversationService(session, ArqResearchJobQueue()).retry_run(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            conversation_id=conversation_id,
            research_run_id=research_run_id,
        )
    except ResearchError as exc:
        raise _research_error_response(exc) from exc


@router.post(
    "/{collection_id}/conversations/{conversation_id}/research-runs/{research_run_id}/cancel",
    response_model=ResearchRunResponse,
    summary="取消尚未开始的研究运行",
)
async def cancel_research_run(
    collection_id: UUID,
    conversation_id: UUID,
    research_run_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResearchRunResponse:
    """只允许取消 queued 任务，已开始的模型调用不会被错误伪装为已取消。"""
    try:
        return await ResearchConversationService(session).cancel_run(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            conversation_id=conversation_id,
            research_run_id=research_run_id,
        )
    except ResearchError as exc:
        raise _research_error_response(exc) from exc


@router.delete(
    "/{collection_id}/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="删除研究会话",
)
async def delete_conversation(
    collection_id: UUID,
    conversation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConversationResponse:
    """软删除会话，避免用户删除动作破坏已经形成的研究运行审计。"""
    try:
        return await ResearchConversationService(session).delete_conversation(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            conversation_id=conversation_id,
        )
    except ResearchError as exc:
        raise _research_error_response(exc) from exc


def _sse_message(event_name: str, event_id: str, payload: dict[str, object]) -> str:
    """将模型无关的公开阶段编码为浏览器可恢复的 SSE 消息。"""
    return (
        f"id: {event_id}\nevent: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    )
