"""研究工作流契约、阶段转换和 Redis 会话键的离线测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID, uuid4

import pytest
from app.db.models.collection import ResearchCollection
from app.modules.workflow.contracts import (
    DirectionQueryPlan,
    ProviderSearchQuery,
    ResearchDirection,
    ResearchLanguage,
    ResearchPlanDraft,
    ResearchScope,
    WorkflowError,
    WorkflowErrorCode,
)
from app.modules.workflow.search_session import (
    SEARCH_SESSION_KEY_PREFIX,
    build_search_session_key,
)
from app.modules.workflow.service import ResearchWorkflowService
from app.modules.workflow.state import WorkspaceWorkflowStage
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000301")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000302")


class FakeSession:
    """工作流服务所需的最小异步会话替身。"""

    def __init__(self, scalar_values: list[object | None]) -> None:
        self._scalar_values = iter(scalar_values)
        self.commit_count = 0
        self.refresh_count = 0

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self

    async def scalar(self, _statement: object) -> object | None:
        return next(self._scalar_values)

    async def commit(self) -> None:
        self.commit_count += 1

    async def refresh(self, _instance: object) -> None:
        self.refresh_count += 1


def _collection(
    *,
    status: str = "active",
    workflow_stage: str = "draft",
) -> ResearchCollection:
    """构造属于当前测试用户的工作区，不依赖数据库。"""
    return ResearchCollection(
        id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        name="Literature review",
        status=status,
        workflow_stage=workflow_stage,
    )


@pytest.mark.asyncio
async def test_transition_persists_only_the_next_legal_stage() -> None:
    """草稿只能进入解析中，服务会在提交后刷新实体供 API 返回。"""
    collection = _collection()
    session = FakeSession([collection])

    result = await ResearchWorkflowService(cast(AsyncSession, session)).transition_collection_stage(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        target_stage=WorkspaceWorkflowStage.ANALYZING,
    )

    assert result is collection
    assert collection.workflow_stage == WorkspaceWorkflowStage.ANALYZING.value
    assert session.commit_count == 1
    assert session.refresh_count == 1


@pytest.mark.asyncio
async def test_transition_rejects_skipping_plan_confirmation() -> None:
    """客户端不能从草稿直接跳到文献检索，避免绕过用户确认。"""
    session = FakeSession([_collection()])

    with pytest.raises(WorkflowError) as error:
        await ResearchWorkflowService(cast(AsyncSession, session)).transition_collection_stage(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            target_stage=WorkspaceWorkflowStage.RETRIEVING,
        )

    assert error.value.code is WorkflowErrorCode.INVALID_STAGE_TRANSITION
    assert session.commit_count == 0


@pytest.mark.asyncio
async def test_transition_hides_foreign_workspace_and_blocks_archived_workspace() -> None:
    """越权工作区统一不存在，归档工作区则明确不能再推进。"""
    missing = FakeSession([None])
    with pytest.raises(WorkflowError) as missing_error:
        await ResearchWorkflowService(cast(AsyncSession, missing)).transition_collection_stage(
            owner_user_id=_OWNER_ID,
            collection_id=uuid4(),
            target_stage=WorkspaceWorkflowStage.ANALYZING,
        )
    assert missing_error.value.code is WorkflowErrorCode.COLLECTION_NOT_FOUND

    archived = FakeSession([_collection(status="archived")])
    with pytest.raises(WorkflowError) as archived_error:
        await ResearchWorkflowService(cast(AsyncSession, archived)).transition_collection_stage(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            target_stage=WorkspaceWorkflowStage.ANALYZING,
        )
    assert archived_error.value.code is WorkflowErrorCode.COLLECTION_NOT_ACTIVE


@pytest.mark.asyncio
async def test_repeated_worker_event_for_current_stage_is_idempotent() -> None:
    """同一阶段的重复消息不再次写库，适配 arq 的至少一次投递语义。"""
    collection = _collection(workflow_stage="analyzing")
    session = FakeSession([collection])

    result = await ResearchWorkflowService(cast(AsyncSession, session)).transition_collection_stage(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        target_stage=WorkspaceWorkflowStage.ANALYZING,
    )

    assert result is collection
    assert session.commit_count == 0
    assert session.refresh_count == 0


def test_scope_requires_a_complete_non_future_custom_year_range() -> None:
    """前端自定义时间范围必须有起止年份，且结束年份不允许超过当前年份。"""
    with pytest.raises(ValidationError, match="同时填写起始年份和结束年份"):
        ResearchScope(start_year=2020)

    with pytest.raises(ValidationError, match="不能晚于当前年份"):
        ResearchScope(start_year=2020, end_year=9999)

    scope = ResearchScope(
        start_year=2020,
        end_year=2024,
        languages=[ResearchLanguage.ENGLISH],
    )
    assert scope.start_year == 2020
    assert scope.languages == ["en"]


def test_plan_draft_requires_two_or_three_distinct_directions() -> None:
    """意图分析器不能只返回一个答案，也不能用重复方向伪造多个选择。"""
    first = ResearchDirection(
        id="review-impact",
        title="城乡公共空间与心理健康",
        summary="比较不同公共空间特征与心理健康结果之间的关联。",
        subtopics=["绿地可达性"],
    )

    with pytest.raises(ValidationError, match="至少应有 2 个"):
        ResearchPlanDraft(
            direction_options=[first],
            suggested_scope=ResearchScope(
                languages=[ResearchLanguage.CHINESE, ResearchLanguage.ENGLISH]
            ),
            direction_query_plans=[],
        )

    duplicate = first.model_copy(update={"title": "重复方向"})
    with pytest.raises(ValidationError, match="标识不能重复"):
        ResearchPlanDraft(
            direction_options=[first, duplicate],
            suggested_scope=ResearchScope(
                languages=[ResearchLanguage.CHINESE, ResearchLanguage.ENGLISH]
            ),
            direction_query_plans=[
                DirectionQueryPlan(
                    direction_id="review-impact",
                    queries=[
                        ProviderSearchQuery(
                            provider="openalex",
                            query="public space mental health",
                        )
                    ],
                )
            ],
        )


def test_search_session_key_only_contains_the_server_generated_run_id() -> None:
    """Redis 键固定按运行 UUID 构造，不拼接用户输入或论文标题。"""
    run_id = UUID("00000000-0000-0000-0000-000000000303")
    assert build_search_session_key(run_id) == f"{SEARCH_SESSION_KEY_PREFIX}:{run_id}"
