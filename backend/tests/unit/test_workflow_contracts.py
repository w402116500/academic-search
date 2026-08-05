"""研究工作流契约、阶段转换和 Redis 会话键的离线测试。"""

from __future__ import annotations

from uuid import UUID

import pytest
from app.modules.research.plan_contracts import (
    DirectionQueryPlan,
    ProviderSearchQuery,
    ResearchDirection,
    ResearchLanguage,
    ResearchPlanDraft,
    ResearchScope,
)
from app.modules.research.state import (
    InvalidWorkflowTransition,
    WorkspaceWorkflowStage,
    assert_workflow_transition,
)
from app.modules.search.session import SEARCH_SESSION_KEY_PREFIX, build_search_session_key
from pydantic import ValidationError


def test_transition_contract_rejects_skipping_plan_confirmation() -> None:
    """客户端不能从草稿直接跳到文献检索，避免绕过用户确认。"""
    assert_workflow_transition(
        WorkspaceWorkflowStage.DRAFT,
        WorkspaceWorkflowStage.ANALYZING,
    )
    with pytest.raises(InvalidWorkflowTransition):
        assert_workflow_transition(
            WorkspaceWorkflowStage.DRAFT,
            WorkspaceWorkflowStage.RETRIEVING,
        )


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
