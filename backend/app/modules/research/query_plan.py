"""已确认研究计划中查询与范围字段的唯一读取入口。"""

from __future__ import annotations

from typing import Any, Protocol

from app.modules.research.plan_contracts import ProviderSearchQuery, ResearchScope


class ConfirmedResearchPlan(Protocol):
    """Only the confirmed-plan fields needed to execute a search."""

    @property
    def status(self) -> str:
        """Durable plan status used to fence search execution."""
        ...

    @property
    def query_plan(self) -> dict[str, Any]:
        """Confirmed provider query payload."""
        ...

    @property
    def scope(self) -> dict[str, Any]:
        """Confirmed time and language scope payload."""
        ...


def read_confirmed_query_plan(
    plan: ConfirmedResearchPlan,
) -> tuple[list[ProviderSearchQuery], ResearchScope]:
    """验证并读取用户确认过的查询表达式和时间、语言范围。"""
    if plan.status != "confirmed":
        raise ValueError("研究计划尚未确认，不能执行文献检索。")
    raw_queries = plan.query_plan.get("queries")
    confirmed_scope = plan.scope.get("confirmed")
    if not isinstance(raw_queries, list) or not raw_queries:
        raise ValueError("已确认研究计划缺少可执行查询。")
    if not isinstance(confirmed_scope, dict):
        raise ValueError("已确认研究计划缺少检索范围。")
    return (
        [ProviderSearchQuery.model_validate(item) for item in raw_queries],
        ResearchScope.model_validate(confirmed_scope),
    )
