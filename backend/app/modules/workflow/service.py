"""研究工作流阶段的权限检查与事务性转换服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.modules.workflow.contracts import WorkflowError, WorkflowErrorCode
from app.modules.workflow.state import (
    InvalidWorkflowTransition,
    WorkspaceWorkflowStage,
    assert_workflow_transition,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.db.models.collection import ResearchCollection


class ResearchWorkflowService:
    """集中维护工作区研究阶段，阻止 API 或 Worker 跳过确认步骤。

    该服务只管理 ``workflow_stage``；工作区的 ``active / archived`` 生命周期
    仍由 ``ResearchWorkspaceService`` 负责，因此两类状态不会互相覆盖。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def transition_collection_stage(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        target_stage: WorkspaceWorkflowStage,
    ) -> ResearchCollection:
        """验证所有权、锁定工作区并持久化一次合法的阶段转换。

        同一目标阶段的重复事件按幂等成功处理，避免浏览器重试或 at-least-once
        Worker 投递造成重复写入；其他跳跃仍会明确失败。
        """
        collection = await self._get_owned_collection_for_update(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
        )

        if collection.status != "active":
            raise WorkflowError(
                WorkflowErrorCode.COLLECTION_NOT_ACTIVE,
                "研究工作区已归档，不能推进研究流程。",
            )

        current_stage = WorkspaceWorkflowStage(collection.workflow_stage)
        if current_stage is target_stage:
            return collection

        try:
            assert_workflow_transition(current_stage, target_stage)
        except InvalidWorkflowTransition as exc:
            raise WorkflowError(WorkflowErrorCode.INVALID_STAGE_TRANSITION, str(exc)) from exc

        collection.workflow_stage = target_stage.value
        await self._session.commit()
        await self._session.refresh(collection)
        return collection

    async def _get_owned_collection_for_update(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
    ) -> ResearchCollection:
        """读取并锁定当前用户的工作区，不泄漏其他用户工作区的存在。"""
        # 延迟导入避免 ORM 模型导入阶段因 package ``__init__`` 发生循环依赖。
        from app.db.models.collection import ResearchCollection

        statement = (
            select(ResearchCollection)
            .where(
                ResearchCollection.id == collection_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status.in_(("active", "archived")),
            )
            .with_for_update()
        )
        collection = await self._session.scalar(statement)
        if collection is None:
            raise WorkflowError(WorkflowErrorCode.COLLECTION_NOT_FOUND, "研究工作区不存在。")
        return collection
