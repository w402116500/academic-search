"""搜索候选全文获取与短期状态查询路由。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from app.api.deps.auth import get_current_user
from app.core.settings import get_literature_source_settings
from app.db.models.user import User
from app.db.session import get_db_session
from app.modules.collections import (
    CollectionAdmissionError,
    CollectionAdmissionErrorCode,
    CollectionAdmissionResult,
    ResearchCollectionAdmissionService,
)
from app.modules.fulltext import Boto3StagingObjectStorage, get_fulltext_acquisition_settings
from app.modules.workflow.contracts import (
    CandidateFulltextError,
    CandidateFulltextErrorCode,
    CandidateFulltextResponse,
    SearchRunError,
    SearchRunErrorCode,
)
from app.modules.workflow.fulltext_service import (
    CandidateFulltextService,
    CandidateFulltextSubmission,
)
from app.modules.workflow.job_queue import ArqCandidateFulltextJobQueue
from app.modules.workflow.search_session import SearchSessionStore
from app.workers.redis import redis_client_from_environment
from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

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


async def _service_with_redis(
    session: AsyncSession,
) -> tuple[CandidateFulltextService, Redis]:
    """创建一次请求范围的 Redis 会话存储，并把连接交给路由 finally 关闭。"""
    settings = get_literature_source_settings()
    redis = redis_client_from_environment()
    return (
        CandidateFulltextService(
            session,
            SearchSessionStore(redis, ttl_seconds=settings.search_session_ttl_seconds),
            ArqCandidateFulltextJobQueue(),
        ),
        redis,
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
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CandidateFulltextResponse:
    """创建全文异步任务；候选、URL 和题录均从服务端检索会话读取。"""
    service, redis = await _service_with_redis(session)
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
    finally:
        await redis.aclose()
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
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CandidateFulltextResponse:
    """仅为可重试的终态失败创建新的全文下载尝试。"""
    service, redis = await _service_with_redis(session)
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
    finally:
        await redis.aclose()
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
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CandidateFulltextResponse:
    """供前端轮询正在下载、校验或已可加入集合的候选全文。"""
    service, redis = await _service_with_redis(session)
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
    finally:
        await redis.aclose()
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
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CollectionAdmissionResult:
    """只接受当前搜索会话中已校验 PDF，创建不会被 Worker 提前领取的 pending 运行。"""
    service, redis = await _service_with_redis(session)
    # rollback 会使 ORM 实体过期；在结束只读事务前保留纯 UUID，之后不能再访问
    # current_user 的懒加载属性，以免异步请求中触发额外的数据库 IO。
    owner_user_id = current_user.id
    try:
        submission = await service.get_state(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
        )
        # get_state 只读查询会让 AsyncSession 自动开启事务；准入服务
        # 需要建立自己的事务边界，先回滚这次无写入的读取事务，避免
        # SQLAlchemy 抛出 "A transaction is already begun"。
        await session.rollback()
        return await ResearchCollectionAdmissionService(
            session,
            Boto3StagingObjectStorage(get_fulltext_acquisition_settings()),
        ).admit(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            candidate=submission.state.candidate,
            fulltext_result=submission.state.result,
        )
    except CandidateFulltextError as exc:
        raise _fulltext_error_response(exc) from exc
    except SearchRunError as exc:
        raise _search_run_error_response(exc) from exc
    except CollectionAdmissionError as exc:
        raise _admission_error_response(exc) from exc
    finally:
        await redis.aclose()
