"""从首页研究要求到计划确认的 API 路由。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from app.api.deps.auth import get_current_user
from app.db.models.user import User
from app.db.session import get_db_session
from app.modules.workflow.contracts import (
    ConfirmResearchPlanRequest,
    RegenerateResearchPlanRequest,
    ResearchPlanError,
    ResearchPlanErrorCode,
    ResearchPlanResponse,
    ResearchSubmissionResponse,
    StartResearchRequest,
)
from app.modules.workflow.job_queue import ArqResearchPlanJobQueue
from app.modules.workflow.plan_service import ResearchPlanService
from app.modules.workflow.state import WorkspaceWorkflowStage
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/collections", tags=["研究计划"])


def _research_plan_error_response(error: ResearchPlanError) -> HTTPException:
    """将领域错误映射为不泄漏资源归属、便于前端分支处理的 HTTP 响应。"""
    if error.code is ResearchPlanErrorCode.COLLECTION_NOT_FOUND:
        status_code = status.HTTP_404_NOT_FOUND
    elif error.code is ResearchPlanErrorCode.QUEUE_UNAVAILABLE:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
    )


@router.post(
    "/research",
    response_model=ResearchSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="提交研究要求并开始意图分析",
)
async def start_research(
    request: StartResearchRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResearchSubmissionResponse:
    """创建可恢复的工作区和首版计划，返回后由 arq 异步完成意图分析。"""
    try:
        submission = await ResearchPlanService(session, ArqResearchPlanJobQueue()).start_research(
            owner_user_id=current_user.id,
            request=request,
        )
    except ResearchPlanError as exc:
        raise _research_plan_error_response(exc) from exc

    return ResearchSubmissionResponse(
        workspace_id=submission.collection.id,
        workflow_stage=WorkspaceWorkflowStage(submission.collection.workflow_stage),
        plan=ResearchPlanResponse.model_validate(submission.plan),
    )


@router.get(
    "/{collection_id}/plan",
    response_model=ResearchPlanResponse,
    summary="读取当前研究计划",
)
async def get_current_research_plan(
    collection_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResearchPlanResponse:
    """刷新页面后从服务端恢复最新计划版本和其生成状态。"""
    try:
        plan = await ResearchPlanService(session).get_current_plan(
            owner_user_id=current_user.id,
            collection_id=collection_id,
        )
    except ResearchPlanError as exc:
        raise _research_plan_error_response(exc) from exc
    return ResearchPlanResponse.model_validate(plan)


@router.post(
    "/{collection_id}/plan/regenerate",
    response_model=ResearchPlanResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="修改研究要求并重新生成计划",
)
async def regenerate_research_plan(
    collection_id: UUID,
    request: RegenerateResearchPlanRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResearchPlanResponse:
    """创建新版本，不覆盖已失败、已确认或已审核的历史计划。"""
    try:
        plan = await ResearchPlanService(session, ArqResearchPlanJobQueue()).regenerate_plan(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            request=request,
        )
    except ResearchPlanError as exc:
        raise _research_plan_error_response(exc) from exc
    return ResearchPlanResponse.model_validate(plan)


@router.post(
    "/{collection_id}/plan/confirm",
    response_model=ResearchPlanResponse,
    summary="确认研究方向与检索范围",
)
async def confirm_research_plan(
    collection_id: UUID,
    request: ConfirmResearchPlanRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResearchPlanResponse:
    """固化用户选择；下一阶段才会据此创建唯一的文献检索运行。"""
    try:
        plan = await ResearchPlanService(session).confirm_current_plan(
            owner_user_id=current_user.id,
            collection_id=collection_id,
            request=request,
        )
    except ResearchPlanError as exc:
        raise _research_plan_error_response(exc) from exc
    return ResearchPlanResponse.model_validate(plan)
