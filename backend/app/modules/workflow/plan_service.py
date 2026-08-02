"""研究工作区草稿、可版本化计划和确认操作的事务编排。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.db.models.collection import ResearchCollection
from app.db.models.workflow import ResearchPlan
from app.modules.workflow.contracts import (
    ConfirmResearchPlanRequest,
    RegenerateResearchPlanRequest,
    ResearchPlanError,
    ResearchPlanErrorCode,
    StartResearchRequest,
)
from app.modules.workflow.intent_analysis import IntentAnalysisResult
from app.modules.workflow.job_queue import ResearchPlanJobQueue, ResearchPlanQueueError
from app.modules.workflow.state import ResearchPlanStatus, WorkspaceWorkflowStage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ResearchWorkspaceSubmission:
    """首页提交后创建的工作区和首个处于解析中的计划版本。"""

    collection: ResearchCollection
    plan: ResearchPlan


class ResearchPlanService:
    """维护计划版本和确认边界，不执行模型调用或实际文献检索。"""

    def __init__(
        self,
        session: AsyncSession,
        queue: ResearchPlanJobQueue | None = None,
    ) -> None:
        self._session = session
        self._queue = queue

    async def start_research(
        self,
        *,
        owner_user_id: UUID,
        request: StartResearchRequest,
    ) -> ResearchWorkspaceSubmission:
        """原子写入工作区草稿和第一版计划，再投递异步意图分析任务。"""
        collection = ResearchCollection(
            id=uuid4(),
            owner_user_id=owner_user_id,
            name=self._workspace_name_from_request(request.raw_request),
            research_question=request.raw_request,
            status="active",
            workflow_stage=WorkspaceWorkflowStage.ANALYZING.value,
        )
        plan = ResearchPlan(
            id=uuid4(),
            collection_id=collection.id,
            revision=1,
            raw_request=request.raw_request,
            status=ResearchPlanStatus.GENERATING.value,
            direction_options=[],
            scope={},
            query_plan={},
            model_snapshot={},
        )
        self._session.add_all((collection, plan))
        await self._session.commit()
        await self._session.refresh(collection)
        await self._session.refresh(plan)

        await self._enqueue_analysis_or_mark_failed(collection=collection, plan=plan)
        return ResearchWorkspaceSubmission(collection=collection, plan=plan)

    async def regenerate_plan(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        request: RegenerateResearchPlanRequest,
    ) -> ResearchPlan:
        """保留旧计划版本，创建新版本并重新进入意图分析阶段。"""
        collection, previous_plan = await self._get_current_plan_and_collection_for_update(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            require_active=True,
        )
        if previous_plan.status == ResearchPlanStatus.GENERATING.value:
            raise ResearchPlanError(
                ResearchPlanErrorCode.ANALYSIS_ALREADY_RUNNING,
                "当前研究计划仍在解析中，请等待完成后再重新生成。",
            )

        previous_plan.status = ResearchPlanStatus.SUPERSEDED.value
        collection.research_question = request.raw_request
        collection.workflow_stage = WorkspaceWorkflowStage.ANALYZING.value
        plan = ResearchPlan(
            id=uuid4(),
            collection_id=collection.id,
            revision=previous_plan.revision + 1,
            raw_request=request.raw_request,
            status=ResearchPlanStatus.GENERATING.value,
            direction_options=[],
            scope={},
            query_plan={},
            model_snapshot={},
        )
        self._session.add(plan)
        await self._session.commit()
        await self._session.refresh(plan)

        await self._enqueue_analysis_or_mark_failed(collection=collection, plan=plan)
        return plan

    async def get_current_plan(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
    ) -> ResearchPlan:
        """读取当前用户工作区的最新计划版本，归档工作区仍可查看历史。"""
        _collection, plan = await self._get_current_plan_and_collection(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            require_active=False,
        )
        return plan

    async def confirm_current_plan(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        request: ConfirmResearchPlanRequest,
    ) -> ResearchPlan:
        """固定用户选择的方向、时间与语言，但不在此处启动检索任务。"""
        _collection, plan = await self._get_current_plan_and_collection_for_update(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            require_active=True,
        )
        confirmed_scope = request.scope.model_dump(mode="json")
        if plan.status == ResearchPlanStatus.CONFIRMED.value:
            if (
                plan.selected_direction_id == request.selected_direction_id
                and plan.scope.get("confirmed") == confirmed_scope
            ):
                return plan
            raise ResearchPlanError(
                ResearchPlanErrorCode.PLAN_ALREADY_CONFIRMED,
                "当前研究计划已经确认；如需修改请重新生成计划。",
            )
        if plan.status != ResearchPlanStatus.READY.value:
            raise ResearchPlanError(
                ResearchPlanErrorCode.PLAN_NOT_READY,
                "研究计划尚未生成完成，不能确认。",
            )

        direction_ids = {
            str(direction.get("id"))
            for direction in plan.direction_options
            if isinstance(direction, dict) and isinstance(direction.get("id"), str)
        }
        if request.selected_direction_id not in direction_ids:
            raise ResearchPlanError(
                ResearchPlanErrorCode.DIRECTION_NOT_FOUND,
                "所选研究方向不属于当前计划版本。",
            )

        selected_queries = self._selected_queries(plan, request.selected_direction_id)
        admission_rules = plan.query_plan.get("admission_rules", {})
        plan.selected_direction_id = request.selected_direction_id
        plan.scope = {
            "suggested": plan.scope.get("suggested", {}),
            "confirmed": confirmed_scope,
            "admission_rules": admission_rules,
        }
        # 确认后只保留选中方向的可执行查询，防止后续 Worker 误用其他候选方向。
        plan.query_plan = {
            "selected_direction_id": request.selected_direction_id,
            "queries": selected_queries,
            "admission_rules": admission_rules,
        }
        plan.status = ResearchPlanStatus.CONFIRMED.value
        plan.confirmed_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(plan)
        return plan

    async def get_plan_for_analysis(self, research_plan_id: UUID) -> ResearchPlan | None:
        """Worker 领取仍在生成中的计划；完成事件重复到达时安全忽略。"""
        statement = select(ResearchPlan).where(
            ResearchPlan.id == research_plan_id,
            ResearchPlan.status == ResearchPlanStatus.GENERATING.value,
        )
        return await self._session.scalar(statement)

    async def complete_analysis(
        self,
        *,
        research_plan_id: UUID,
        result: IntentAnalysisResult,
    ) -> ResearchPlan | None:
        """将已校验模型结果写入计划，并把工作区推进到计划确认阶段。"""
        collection, plan = await self._get_plan_and_collection_by_id_for_update(research_plan_id)
        if collection is None or plan is None:
            return None
        if plan.status == ResearchPlanStatus.READY.value:
            return plan
        if plan.status != ResearchPlanStatus.GENERATING.value:
            return None
        if collection.status != "active":
            raise ResearchPlanError(
                ResearchPlanErrorCode.COLLECTION_NOT_ACTIVE,
                "研究工作区已归档，不能写入新的意图分析结果。",
            )

        plan.direction_options = [
            direction.model_dump(mode="json") for direction in result.draft.direction_options
        ]
        plan.scope = {"suggested": result.draft.suggested_scope.model_dump(mode="json")}
        plan.query_plan = self._serialize_direction_query_plans(result)
        plan.model_snapshot = dict(result.model_snapshot)
        plan.error_code = None
        plan.error_message = None
        plan.status = ResearchPlanStatus.READY.value
        collection.workflow_stage = WorkspaceWorkflowStage.PLAN_REVIEW.value
        await self._session.commit()
        await self._session.refresh(plan)
        return plan

    async def fail_analysis(
        self,
        *,
        research_plan_id: UUID,
        error_code: str,
        error_message: str,
    ) -> ResearchPlan | None:
        """记录可展示错误并让工作区进入失败阶段，等待用户重新生成。"""
        collection, plan = await self._get_plan_and_collection_by_id_for_update(research_plan_id)
        if collection is None or plan is None or plan.status != ResearchPlanStatus.GENERATING.value:
            return plan

        plan.status = ResearchPlanStatus.FAILED.value
        plan.error_code = error_code
        plan.error_message = error_message
        if collection.status == "active":
            collection.workflow_stage = WorkspaceWorkflowStage.FAILED.value
        await self._session.commit()
        await self._session.refresh(plan)
        return plan

    async def _enqueue_analysis_or_mark_failed(
        self,
        *,
        collection: ResearchCollection,
        plan: ResearchPlan,
    ) -> None:
        """先提交计划记录再投递队列；队列失败时留下可恢复的失败状态。"""
        if self._queue is None:
            raise RuntimeError("创建或重新生成研究计划时必须提供任务队列")
        try:
            plan.arq_job_id = await self._queue.enqueue_analysis(plan.id)
            await self._session.commit()
            await self._session.refresh(plan)
        except ResearchPlanQueueError as exc:
            # 计划已经落库，不能回滚为“似乎未提交”；明确失败才能让用户重新生成。
            message = "研究意图分析任务无法投递，请稍后重新生成计划。"
            plan.status = ResearchPlanStatus.FAILED.value
            plan.error_code = ResearchPlanErrorCode.QUEUE_UNAVAILABLE.value
            plan.error_message = message
            collection.workflow_stage = WorkspaceWorkflowStage.FAILED.value
            await self._session.commit()
            await self._session.refresh(plan)
            raise ResearchPlanError(
                ResearchPlanErrorCode.QUEUE_UNAVAILABLE,
                message,
            ) from exc

    async def _get_current_plan_and_collection(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        require_active: bool,
        lock: bool = False,
    ) -> tuple[ResearchCollection, ResearchPlan]:
        """按版本读取工作区最新计划，越权工作区统一按不存在处理。"""
        statement = (
            select(ResearchCollection, ResearchPlan)
            .join(ResearchPlan, ResearchPlan.collection_id == ResearchCollection.id)
            .where(
                ResearchCollection.id == collection_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status.in_(("active", "archived")),
            )
            .order_by(ResearchPlan.revision.desc())
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update(of=(ResearchCollection, ResearchPlan))
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            raise ResearchPlanError(
                ResearchPlanErrorCode.COLLECTION_NOT_FOUND,
                "研究工作区或其研究计划不存在。",
            )
        collection, plan = row._tuple()
        if require_active and collection.status != "active":
            raise ResearchPlanError(
                ResearchPlanErrorCode.COLLECTION_NOT_ACTIVE,
                "研究工作区已归档，不能修改研究计划。",
            )
        return collection, plan

    async def _get_current_plan_and_collection_for_update(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        require_active: bool,
    ) -> tuple[ResearchCollection, ResearchPlan]:
        """为重新生成和确认操作锁定工作区及其当前计划版本。"""
        return await self._get_current_plan_and_collection(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            require_active=require_active,
            lock=True,
        )

    async def _get_plan_and_collection_by_id_for_update(
        self, research_plan_id: UUID
    ) -> tuple[ResearchCollection | None, ResearchPlan | None]:
        """Worker 按计划标识锁定数据；找不到时返回空以适配过期队列消息。"""
        statement = (
            select(ResearchCollection, ResearchPlan)
            .join(ResearchPlan, ResearchPlan.collection_id == ResearchCollection.id)
            .where(ResearchPlan.id == research_plan_id)
            .with_for_update(of=(ResearchCollection, ResearchPlan))
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            # 已删除工作区留下的旧队列消息不应该恢复已删除的数据。
            return None, None
        collection, plan = row._tuple()
        return collection, plan

    @staticmethod
    def _serialize_direction_query_plans(result: IntentAnalysisResult) -> dict[str, Any]:
        """把 Pydantic 草稿写成 JSONB 结构，后续确认时按方向稳定提取。"""
        return {
            "by_direction": {
                plan.direction_id: [query.model_dump(mode="json") for query in plan.queries]
                for plan in result.draft.direction_query_plans
            },
            # 准入规则由服务端定义，模型只能生成检索词，不能放宽正式文献边界。
            "admission_rules": {
                "doi_required": True,
                "citation_required": True,
                "fulltext_required": True,
            },
        }

    @staticmethod
    def _selected_queries(plan: ResearchPlan, direction_id: str) -> list[dict[str, Any]]:
        """从已校验草稿中取出选中方向的查询，异常状态绝不继续执行。"""
        by_direction = plan.query_plan.get("by_direction")
        if not isinstance(by_direction, dict):
            raise ResearchPlanError(
                ResearchPlanErrorCode.PLAN_DATA_INVALID,
                "研究计划缺少方向对应的检索表达式，无法确认。",
            )
        queries = by_direction.get(direction_id)
        if not isinstance(queries, list) or not queries:
            raise ResearchPlanError(
                ResearchPlanErrorCode.PLAN_DATA_INVALID,
                "所选研究方向缺少可执行检索表达式，无法确认。",
            )
        if not all(isinstance(query, dict) for query in queries):
            raise ResearchPlanError(
                ResearchPlanErrorCode.PLAN_DATA_INVALID,
                "研究计划中的检索表达式格式不正确，无法确认。",
            )
        return queries

    @staticmethod
    def _workspace_name_from_request(raw_request: str) -> str:
        """从首页输入生成可扫描的工作区名称，避免引入第二个必填表单。"""
        normalized = " ".join(raw_request.split())
        visible_name = normalized[:80].rstrip("，,。.;；:：")
        return visible_name or "未命名研究"
