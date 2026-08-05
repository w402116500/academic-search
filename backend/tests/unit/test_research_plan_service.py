"""研究工作区创建、计划版本和确认边界的离线服务测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.modules.research.intent_analysis import IntentAnalysisResult
from app.modules.research.plan_contracts import (
    ConfirmResearchPlanRequest,
    DirectionQueryPlan,
    ProviderSearchQuery,
    RegenerateResearchPlanRequest,
    ResearchDirection,
    ResearchLanguage,
    ResearchPlanDraft,
    ResearchPlanError,
    ResearchPlanErrorCode,
    ResearchScope,
    StartResearchRequest,
)
from app.modules.research.plan_models import (
    ResearchPlanContext,
    ResearchPlanRecord,
    ResearchPlanWorkspace,
)
from app.modules.research.plan_repository import (
    CreateInitialResearchPlan,
    CreateResearchPlanRevision,
)
from app.modules.research.plan_service import ResearchPlanService
from app.modules.research.queue import ResearchPlanQueueError
from app.modules.research.state import ResearchPlanStatus, WorkspaceWorkflowStage

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000401")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000402")
_PLAN_ID = UUID("00000000-0000-0000-0000-000000000403")


class FakeResearchPlanRepository:
    """In-memory plan port replacement that preserves the latest saved context."""

    def __init__(self, context: ResearchPlanContext | None = None) -> None:
        self.current = context
        self.created_commands: list[CreateInitialResearchPlan] = []
        self.revision_inputs: list[ResearchPlanContext] = []
        self.revision_commands: list[CreateResearchPlanRevision] = []
        self.saved_contexts: list[ResearchPlanContext] = []

    async def create_initial(self, command: CreateInitialResearchPlan) -> ResearchPlanContext:
        self.created_commands.append(command)
        now = datetime.now(UTC)
        self.current = ResearchPlanContext(
            workspace=ResearchPlanWorkspace(
                id=command.workspace_id,
                owner_user_id=command.owner_user_id,
                name=command.workspace_name,
                description=None,
                research_question=command.raw_request,
                status="active",
                workflow_stage=WorkspaceWorkflowStage.ANALYZING.value,
                created_at=now,
                updated_at=now,
            ),
            plan=_new_plan(
                plan_id=command.plan_id,
                collection_id=command.workspace_id,
                raw_request=command.raw_request,
            ),
        )
        return self.current

    async def get_current(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        for_update: bool = False,
    ) -> ResearchPlanContext | None:
        del for_update
        if (
            self.current is None
            or self.current.workspace.owner_user_id != owner_user_id
            or self.current.workspace.id != collection_id
        ):
            return None
        return self.current

    async def add_revision(
        self,
        *,
        current: ResearchPlanContext,
        command: CreateResearchPlanRevision,
    ) -> ResearchPlanContext:
        self.revision_inputs.append(current)
        self.revision_commands.append(command)
        self.current = ResearchPlanContext(
            workspace=current.workspace,
            plan=_new_plan(
                plan_id=command.plan_id,
                collection_id=command.collection_id,
                raw_request=command.raw_request,
                revision=command.revision,
            ),
        )
        return self.current

    async def get_generating(self, plan_id: UUID) -> ResearchPlanRecord | None:
        if (
            self.current is not None
            and self.current.plan.id == plan_id
            and self.current.plan.status == ResearchPlanStatus.GENERATING.value
        ):
            return self.current.plan
        return None

    async def get_by_id_for_update(self, plan_id: UUID) -> ResearchPlanContext | None:
        if self.current is None or self.current.plan.id != plan_id:
            return None
        return self.current

    async def save(self, context: ResearchPlanContext) -> ResearchPlanContext:
        self.saved_contexts.append(context)
        self.current = context
        return context


class FakeQueue:
    """可返回固定 Job 或模拟 Redis 投递失败的队列替身。"""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.enqueued_plan_ids: list[UUID] = []

    async def enqueue_analysis(self, research_plan_id: UUID) -> str:
        if self._fail:
            raise ResearchPlanQueueError("Redis unavailable")
        self.enqueued_plan_ids.append(research_plan_id)
        return f"arq-{research_plan_id}"


def _collection(*, workflow_stage: str = "analyzing") -> ResearchPlanWorkspace:
    """构造当前用户拥有的活动工作区。"""
    now = datetime.now(UTC)
    return ResearchPlanWorkspace(
        id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        name="公共空间与心理健康",
        description=None,
        research_question="公共空间如何影响城市居民的心理健康？",
        status="active",
        workflow_stage=workflow_stage,
        created_at=now,
        updated_at=now,
    )


def _new_plan(
    *,
    plan_id: UUID,
    collection_id: UUID,
    raw_request: str,
    revision: int = 1,
) -> ResearchPlanRecord:
    now = datetime.now(UTC)
    return ResearchPlanRecord(
        id=plan_id,
        collection_id=collection_id,
        revision=revision,
        raw_request=raw_request,
        status=ResearchPlanStatus.GENERATING.value,
        direction_options=[],
        selected_direction_id=None,
        scope={},
        query_plan={},
        model_snapshot={},
        arq_job_id=None,
        error_code=None,
        error_message=None,
        confirmed_at=None,
        created_at=now,
        updated_at=now,
    )


def _ready_plan() -> ResearchPlanRecord:
    """构造已完成意图分析、但尚未确认的计划版本。"""
    plan = _new_plan(
        plan_id=_PLAN_ID,
        collection_id=_COLLECTION_ID,
        raw_request="公共空间如何影响城市居民的心理健康？",
    )
    return ResearchPlanRecord(
        id=plan.id,
        collection_id=plan.collection_id,
        revision=plan.revision,
        raw_request=plan.raw_request,
        status=ResearchPlanStatus.READY.value,
        direction_options=[
            {
                "id": "built-environment",
                "title": "建成环境与心理健康",
                "summary": "研究空间特征与心理健康关联。",
                "subtopics": ["绿地可达性"],
            },
            {
                "id": "social-cohesion",
                "title": "社区凝聚力与心理健康",
                "summary": "研究社会互动机制。",
                "subtopics": ["社会互动"],
            },
        ],
        selected_direction_id=None,
        scope={"suggested": {"languages": ["zh", "en"]}},
        query_plan={
            "by_direction": {
                "built-environment": [
                    {"provider": "openalex", "query": "public space mental health"}
                ],
                "social-cohesion": [
                    {"provider": "openalex", "query": "social cohesion mental health"}
                ],
            },
            "admission_rules": {"doi_required": True, "fulltext_required": True},
        },
        model_snapshot={"model": "test"},
        arq_job_id=None,
        error_code=None,
        error_message=None,
        confirmed_at=None,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _context(
    *,
    workspace: ResearchPlanWorkspace | None = None,
    plan: ResearchPlanRecord | None = None,
) -> ResearchPlanContext:
    return ResearchPlanContext(
        workspace=workspace or _collection(workflow_stage="plan_review"),
        plan=plan or _ready_plan(),
    )


@pytest.mark.asyncio
async def test_start_research_creates_recoverable_workspace_plan_and_job() -> None:
    """首页只传研究要求，服务一次创建工作区、计划版本和确定的 arq Job ID。"""
    plans = FakeResearchPlanRepository()
    queue = FakeQueue()

    submission = await ResearchPlanService(plans, queue).start_research(
        owner_user_id=_OWNER_ID,
        request=StartResearchRequest(raw_request="  公共空间\n如何影响心理健康？  "),
    )

    assert len(plans.created_commands) == 1
    assert submission.collection.research_question == "公共空间\n如何影响心理健康？"
    assert submission.collection.workflow_stage == WorkspaceWorkflowStage.ANALYZING.value
    assert submission.plan.revision == 1
    assert submission.plan.status == ResearchPlanStatus.GENERATING.value
    assert submission.plan.arq_job_id == f"arq-{submission.plan.id}"
    assert queue.enqueued_plan_ids == [submission.plan.id]
    assert len(plans.saved_contexts) == 1


@pytest.mark.asyncio
async def test_start_research_records_failed_plan_when_queue_is_unavailable() -> None:
    """队列不可用不能返回虚假的已分析状态，失败工作区仍可由用户恢复。"""
    plans = FakeResearchPlanRepository()

    with pytest.raises(ResearchPlanError) as error:
        await ResearchPlanService(plans, FakeQueue(fail=True)).start_research(
            owner_user_id=_OWNER_ID,
            request=StartResearchRequest(raw_request="公共空间如何影响心理健康？"),
        )

    assert error.value.code is ResearchPlanErrorCode.QUEUE_UNAVAILABLE
    assert plans.current is not None
    assert plans.current.workspace.workflow_stage == WorkspaceWorkflowStage.FAILED.value
    assert plans.current.plan.status == ResearchPlanStatus.FAILED.value
    assert len(plans.saved_contexts) == 1


@pytest.mark.asyncio
async def test_confirm_plan_selects_only_one_direction_and_is_idempotent() -> None:
    """确认只留下选中方向查询；相同重试不重复提交或改变已确认计划。"""
    collection = _collection(workflow_stage="plan_review")
    plan = _ready_plan()
    scope = ResearchScope(languages=[ResearchLanguage.ENGLISH])
    request = ConfirmResearchPlanRequest(selected_direction_id="built-environment", scope=scope)
    plans = FakeResearchPlanRepository(ResearchPlanContext(collection, plan))
    service = ResearchPlanService(plans)

    confirmed = await service.confirm_current_plan(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        request=request,
    )
    repeated = await service.confirm_current_plan(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        request=request,
    )

    assert confirmed == repeated
    assert confirmed.status == ResearchPlanStatus.CONFIRMED.value
    assert confirmed.query_plan["selected_direction_id"] == "built-environment"
    assert confirmed.query_plan["queries"] == [
        {"provider": "openalex", "query": "public space mental health"}
    ]
    assert len(plans.saved_contexts) == 1


@pytest.mark.asyncio
async def test_confirm_plan_rejects_direction_not_in_current_revision() -> None:
    """客户端不能以方向名称或旧版本 ID 覆盖当前计划的检索表达式。"""
    plans = FakeResearchPlanRepository(
        _context(workspace=_collection(workflow_stage="plan_review"))
    )
    service = ResearchPlanService(plans)

    with pytest.raises(ResearchPlanError) as error:
        await service.confirm_current_plan(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            request=ConfirmResearchPlanRequest(
                selected_direction_id="previous-plan-direction",
                scope=ResearchScope(languages=[ResearchLanguage.ENGLISH]),
            ),
        )

    assert error.value.code is ResearchPlanErrorCode.DIRECTION_NOT_FOUND


@pytest.mark.asyncio
async def test_current_plan_hides_another_users_workspace() -> None:
    """计划读取与工作区读取一致，不向当前用户泄漏其他工作区是否存在。"""
    plans = FakeResearchPlanRepository()

    with pytest.raises(ResearchPlanError) as error:
        await ResearchPlanService(plans).get_current_plan(
            owner_user_id=_OWNER_ID,
            collection_id=uuid4(),
        )

    assert error.value.code is ResearchPlanErrorCode.COLLECTION_NOT_FOUND


@pytest.mark.asyncio
async def test_regenerate_supersedes_previous_version_and_requeues_analysis() -> None:
    """修改研究要求生成版本 2，不会覆写已生成的版本 1。"""
    collection = _collection(workflow_stage="plan_review")
    previous_plan = _ready_plan()
    plans = FakeResearchPlanRepository(ResearchPlanContext(collection, previous_plan))
    queue = FakeQueue()

    plan = await ResearchPlanService(plans, queue).regenerate_plan(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        request=RegenerateResearchPlanRequest(raw_request="社区绿地如何影响老年人的心理健康？"),
    )

    assert plans.revision_inputs[0].plan.status == ResearchPlanStatus.SUPERSEDED.value
    assert plan.revision == 2
    assert plan.raw_request == "社区绿地如何影响老年人的心理健康？"
    assert plans.current is not None
    assert plans.current.workspace.workflow_stage == WorkspaceWorkflowStage.ANALYZING.value
    assert queue.enqueued_plan_ids == [plan.id]


@pytest.mark.asyncio
async def test_complete_analysis_persists_validated_direction_specific_queries() -> None:
    """Worker 成功后才进入计划确认页，且查询计划保持方向到查询的映射。"""
    collection = _collection()
    plan = _new_plan(
        plan_id=_PLAN_ID,
        collection_id=_COLLECTION_ID,
        raw_request="公共空间如何影响心理健康？",
    )
    # 复用 Pydantic 解析确保此处只测试持久化，不重复测试模型输出校验。
    draft = ResearchPlanDraft(
        direction_options=[
            ResearchDirection(
                id="built-environment",
                title="建成环境与心理健康",
                summary="研究空间特征。",
                subtopics=["绿地"],
            ),
            ResearchDirection(
                id="social-cohesion",
                title="社区凝聚力与心理健康",
                summary="研究社会互动。",
                subtopics=["互动"],
            ),
        ],
        suggested_scope=ResearchScope(languages=[ResearchLanguage.ENGLISH]),
        direction_query_plans=[
            DirectionQueryPlan(
                direction_id="built-environment",
                queries=[
                    ProviderSearchQuery(
                        provider="openalex",
                        query="public space mental health",
                    )
                ],
            ),
            DirectionQueryPlan(
                direction_id="social-cohesion",
                queries=[
                    ProviderSearchQuery(
                        provider="crossref",
                        query="social cohesion mental health",
                    )
                ],
            ),
        ],
    )
    plans = FakeResearchPlanRepository(ResearchPlanContext(collection, plan))

    completed = await ResearchPlanService(plans).complete_analysis(
        research_plan_id=_PLAN_ID,
        result=IntentAnalysisResult(draft=draft, model_snapshot={"model": "test"}),
    )

    assert completed is not None
    assert completed.status == ResearchPlanStatus.READY.value
    assert plans.current is not None
    assert plans.current.workspace.workflow_stage == WorkspaceWorkflowStage.PLAN_REVIEW.value
    assert set(completed.query_plan["by_direction"]) == {
        "built-environment",
        "social-cohesion",
    }
