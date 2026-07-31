"""研究工作区创建、计划版本和确认边界的离线服务测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID, uuid4

import pytest
from app.db.models.collection import ResearchCollection
from app.db.models.workflow import ResearchPlan
from app.modules.workflow.contracts import (
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
from app.modules.workflow.intent_analysis import IntentAnalysisResult
from app.modules.workflow.job_queue import ResearchPlanQueueError
from app.modules.workflow.plan_service import ResearchPlanService
from app.modules.workflow.state import ResearchPlanStatus, WorkspaceWorkflowStage
from sqlalchemy.ext.asyncio import AsyncSession

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000401")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000402")
_PLAN_ID = UUID("00000000-0000-0000-0000-000000000403")


class FakeRow:
    """模拟 SQLAlchemy 返回的两列 Row，保留服务使用的 ``tuple`` 入口。"""

    def __init__(self, values: tuple[object, object]) -> None:
        self._values = values

    def tuple(self) -> tuple[object, object]:
        return self._values


class FakeExecuteResult:
    """按预设返回单行结果的执行结果替身。"""

    def __init__(self, row: FakeRow | None) -> None:
        self._row = row

    def one_or_none(self) -> FakeRow | None:
        return self._row


class FakeSession:
    """计划服务所需的最小异步会话替身。"""

    def __init__(
        self,
        *,
        execute_rows: list[FakeRow | None] | None = None,
        scalar_values: list[object | None] | None = None,
    ) -> None:
        self._execute_rows = iter(execute_rows or [])
        self._scalar_values = iter(scalar_values or [])
        self.added: list[object] = []
        self.commit_count = 0
        self.refresh_count = 0

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self

    def add(self, value: object) -> None:
        self.added.append(value)

    def add_all(self, values: tuple[object, ...]) -> None:
        self.added.extend(values)

    async def commit(self) -> None:
        self.commit_count += 1

    async def refresh(self, _instance: object) -> None:
        self.refresh_count += 1

    async def execute(self, _statement: object) -> FakeExecuteResult:
        return FakeExecuteResult(next(self._execute_rows))

    async def scalar(self, _statement: object) -> object | None:
        return next(self._scalar_values)


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


def _collection(*, workflow_stage: str = "analyzing") -> ResearchCollection:
    """构造当前用户拥有的活动工作区。"""
    return ResearchCollection(
        id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        name="公共空间与心理健康",
        research_question="公共空间如何影响城市居民的心理健康？",
        status="active",
        workflow_stage=workflow_stage,
    )


def _ready_plan() -> ResearchPlan:
    """构造已完成意图分析、但尚未确认的计划版本。"""
    return ResearchPlan(
        id=_PLAN_ID,
        collection_id=_COLLECTION_ID,
        revision=1,
        raw_request="公共空间如何影响城市居民的心理健康？",
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
    )


@pytest.mark.asyncio
async def test_start_research_creates_recoverable_workspace_plan_and_job() -> None:
    """首页只传研究要求，服务一次创建工作区、计划版本和确定的 arq Job ID。"""
    session = FakeSession()
    queue = FakeQueue()

    submission = await ResearchPlanService(cast(AsyncSession, session), queue).start_research(
        owner_user_id=_OWNER_ID,
        request=StartResearchRequest(raw_request="  公共空间\n如何影响心理健康？  "),
    )

    assert session.added == [submission.collection, submission.plan]
    assert submission.collection.research_question == "公共空间\n如何影响心理健康？"
    assert submission.collection.workflow_stage == WorkspaceWorkflowStage.ANALYZING.value
    assert submission.plan.revision == 1
    assert submission.plan.status == ResearchPlanStatus.GENERATING.value
    assert submission.plan.arq_job_id == f"arq-{submission.plan.id}"
    assert queue.enqueued_plan_ids == [submission.plan.id]
    assert session.commit_count == 2


@pytest.mark.asyncio
async def test_start_research_records_failed_plan_when_queue_is_unavailable() -> None:
    """队列不可用不能返回虚假的已分析状态，失败工作区仍可由用户恢复。"""
    session = FakeSession()

    with pytest.raises(ResearchPlanError) as error:
        await ResearchPlanService(cast(AsyncSession, session), FakeQueue(fail=True)).start_research(
            owner_user_id=_OWNER_ID,
            request=StartResearchRequest(raw_request="公共空间如何影响心理健康？"),
        )

    assert error.value.code is ResearchPlanErrorCode.QUEUE_UNAVAILABLE
    collection, plan = cast(tuple[ResearchCollection, ResearchPlan], tuple(session.added))
    assert collection.workflow_stage == WorkspaceWorkflowStage.FAILED.value
    assert plan.status == ResearchPlanStatus.FAILED.value
    assert session.commit_count == 2


@pytest.mark.asyncio
async def test_confirm_plan_selects_only_one_direction_and_is_idempotent() -> None:
    """确认只留下选中方向查询；相同重试不重复提交或改变已确认计划。"""
    collection = _collection(workflow_stage="plan_review")
    plan = _ready_plan()
    row = FakeRow((collection, plan))
    scope = ResearchScope(languages=[ResearchLanguage.ENGLISH])
    request = ConfirmResearchPlanRequest(selected_direction_id="built-environment", scope=scope)
    session = FakeSession(execute_rows=[row, FakeRow((collection, plan))])
    service = ResearchPlanService(cast(AsyncSession, session))

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

    assert confirmed is repeated
    assert plan.status == ResearchPlanStatus.CONFIRMED.value
    assert plan.query_plan["selected_direction_id"] == "built-environment"
    assert plan.query_plan["queries"] == [
        {"provider": "openalex", "query": "public space mental health"}
    ]
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_confirm_plan_rejects_direction_not_in_current_revision() -> None:
    """客户端不能以方向名称或旧版本 ID 覆盖当前计划的检索表达式。"""
    session = FakeSession(
        execute_rows=[FakeRow((_collection(workflow_stage="plan_review"), _ready_plan()))]
    )
    service = ResearchPlanService(cast(AsyncSession, session))

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
    session = FakeSession(execute_rows=[None])

    with pytest.raises(ResearchPlanError) as error:
        await ResearchPlanService(cast(AsyncSession, session)).get_current_plan(
            owner_user_id=_OWNER_ID,
            collection_id=uuid4(),
        )

    assert error.value.code is ResearchPlanErrorCode.COLLECTION_NOT_FOUND


@pytest.mark.asyncio
async def test_regenerate_supersedes_previous_version_and_requeues_analysis() -> None:
    """修改研究要求生成版本 2，不会覆写已生成的版本 1。"""
    collection = _collection(workflow_stage="plan_review")
    previous_plan = _ready_plan()
    session = FakeSession(execute_rows=[FakeRow((collection, previous_plan))])
    queue = FakeQueue()

    plan = await ResearchPlanService(cast(AsyncSession, session), queue).regenerate_plan(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        request=RegenerateResearchPlanRequest(raw_request="社区绿地如何影响老年人的心理健康？"),
    )

    assert previous_plan.status == ResearchPlanStatus.SUPERSEDED.value
    assert plan.revision == 2
    assert plan.raw_request == "社区绿地如何影响老年人的心理健康？"
    assert collection.workflow_stage == WorkspaceWorkflowStage.ANALYZING.value
    assert queue.enqueued_plan_ids == [plan.id]


@pytest.mark.asyncio
async def test_complete_analysis_persists_validated_direction_specific_queries() -> None:
    """Worker 成功后才进入计划确认页，且查询计划保持方向到查询的映射。"""
    collection = _collection()
    plan = ResearchPlan(
        id=_PLAN_ID,
        collection_id=_COLLECTION_ID,
        revision=1,
        raw_request="公共空间如何影响心理健康？",
        status=ResearchPlanStatus.GENERATING.value,
        direction_options=[],
        scope={},
        query_plan={},
        model_snapshot={},
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
    session = FakeSession(execute_rows=[FakeRow((collection, plan))])

    completed = await ResearchPlanService(cast(AsyncSession, session)).complete_analysis(
        research_plan_id=_PLAN_ID,
        result=IntentAnalysisResult(draft=draft, model_snapshot={"model": "test"}),
    )

    assert completed is plan
    assert plan.status == ResearchPlanStatus.READY.value
    assert collection.workflow_stage == WorkspaceWorkflowStage.PLAN_REVIEW.value
    assert set(plan.query_plan["by_direction"]) == {"built-environment", "social-cohesion"}
