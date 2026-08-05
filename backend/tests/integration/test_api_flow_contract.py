"""首页到检索启动的 FastAPI 路由契约测试。

本测试替换模型、队列和数据库领域服务，只验证 HTTP 路由的顺序、输入边界和
前端需要的恢复字段；Provider 与真实数据库的行为由现有领域和 live 测试覆盖。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from app.api.deps import services
from app.api.deps.auth import get_current_user
from app.infra.db.models.collection import ResearchCollection
from app.infra.db.models.workflow import ResearchPlan, SearchRun
from app.infra.db.session import get_db_session
from app.main import app
from app.modules.research.plan_contracts import ConfirmResearchPlanRequest
from app.modules.research.state import ResearchPlanStatus, WorkspaceWorkflowStage
from app.modules.search.state import SearchRunStage, SearchRunStatus

_USER_ID = UUID("00000000-0000-0000-0000-000000000601")
_WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000602")
_PLAN_ID = UUID("00000000-0000-0000-0000-000000000603")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000604")


def _timestamped(model: Any) -> Any:
    """给 ORM 测试对象补上数据库 server default 会生成的审计时间。"""
    now = datetime.now(UTC)
    model.created_at = now
    model.updated_at = now
    return model


def _fixtures() -> tuple[ResearchCollection, ResearchPlan, SearchRun]:
    """创建一组可跨四个路由步骤复用的内存领域对象。"""
    collection = _timestamped(
        ResearchCollection(
            id=_WORKSPACE_ID,
            owner_user_id=_USER_ID,
            name="研究要求",
            research_question="研究要求",
            status="active",
            workflow_stage=WorkspaceWorkflowStage.ANALYZING.value,
        )
    )
    plan = _timestamped(
        ResearchPlan(
            id=_PLAN_ID,
            collection_id=_WORKSPACE_ID,
            revision=1,
            raw_request="研究要求",
            status=ResearchPlanStatus.GENERATING.value,
            direction_options=[
                {
                    "id": "direction-a",
                    "title": "方向 A",
                    "summary": "用于测试的研究方向。",
                    "subtopics": ["主题"],
                },
                {
                    "id": "direction-b",
                    "title": "方向 B",
                    "summary": "备用研究方向。",
                    "subtopics": ["方法"],
                },
            ],
            scope={"suggested": {}},
            query_plan={"by_direction": {}},
            model_snapshot={"model": "test-model"},
        )
    )
    run = _timestamped(
        SearchRun(
            id=_RUN_ID,
            collection_id=_WORKSPACE_ID,
            research_plan_id=_PLAN_ID,
            status=SearchRunStatus.QUEUED.value,
            stage=SearchRunStage.DISPATCH.value,
            attempt_no=1,
            provider_summary={},
            candidate_counts={},
        )
    )
    return collection, plan, run


@pytest.mark.asyncio
async def test_research_entry_to_search_run_preserves_resume_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前端提交、刷新计划、确认范围和启动检索时始终得到同一工作区标识。"""
    collection, plan, run = _fixtures()

    class FakePlanService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def start_research(self, *, owner_user_id: UUID, request: object) -> object:
            assert owner_user_id == _USER_ID
            return SimpleNamespace(collection=collection, plan=plan)

        async def get_current_plan(
            self, *, owner_user_id: UUID, collection_id: UUID
        ) -> ResearchPlan:
            assert owner_user_id == _USER_ID
            assert collection_id == _WORKSPACE_ID
            return plan

        async def confirm_current_plan(
            self,
            *,
            owner_user_id: UUID,
            collection_id: UUID,
            request: ConfirmResearchPlanRequest,
        ) -> ResearchPlan:
            assert owner_user_id == _USER_ID
            assert collection_id == _WORKSPACE_ID
            plan.status = ResearchPlanStatus.CONFIRMED.value
            plan.selected_direction_id = request.selected_direction_id
            plan.scope = {"confirmed": request.scope.model_dump(mode="json")}
            collection.workflow_stage = WorkspaceWorkflowStage.RETRIEVING.value
            return plan

    class FakeSearchRunService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def start_search(self, *, owner_user_id: UUID, collection_id: UUID) -> object:
            assert owner_user_id == _USER_ID
            assert collection_id == _WORKSPACE_ID
            return SimpleNamespace(search_run=run)

    async def fake_current_user() -> object:
        return SimpleNamespace(id=_USER_ID)

    async def fake_session():
        yield object()

    monkeypatch.setattr(services, "ResearchPlanService", FakePlanService)
    monkeypatch.setattr(services, "ArqResearchPlanJobQueue", lambda: object())
    monkeypatch.setattr(services, "SearchRunService", FakeSearchRunService)
    monkeypatch.setattr(services, "ArqSearchRunJobQueue", lambda: object())
    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_db_session] = fake_session

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            submission = await client.post(
                "/api/v1/collections/research",
                json={"raw_request": "研究要求"},
            )
            assert submission.status_code == 201
            submission_body = submission.json()
            assert submission_body["workspace_id"] == str(_WORKSPACE_ID)
            assert submission_body["workflow_stage"] == "analyzing"
            assert submission_body["plan"]["status"] == "generating"

            refreshed_plan = await client.get(f"/api/v1/collections/{_WORKSPACE_ID}/plan")
            assert refreshed_plan.status_code == 200
            assert refreshed_plan.json()["id"] == str(_PLAN_ID)

            confirmed = await client.post(
                f"/api/v1/collections/{_WORKSPACE_ID}/plan/confirm",
                json={
                    "selected_direction_id": "direction-a",
                    "scope": {"start_year": 2018, "end_year": 2026, "languages": ["en"]},
                },
            )
            assert confirmed.status_code == 200
            assert confirmed.json()["status"] == "confirmed"
            assert confirmed.json()["selected_direction_id"] == "direction-a"

            started = await client.post(f"/api/v1/collections/{_WORKSPACE_ID}/search-runs")
            assert started.status_code == 202
            assert started.json()["collection_id"] == str(_WORKSPACE_ID)
            assert started.json()["research_plan_id"] == str(_PLAN_ID)
            assert started.json()["status"] == "queued"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
