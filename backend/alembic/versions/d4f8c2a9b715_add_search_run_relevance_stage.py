"""允许检索运行持久化候选相关性评估阶段。

Revision ID: d4f8c2a9b715
Revises: c3e7a1b9d426
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "d4f8c2a9b715"
down_revision = "c3e7a1b9d426"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """扩展搜索运行阶段约束，不改变已有运行记录。"""
    # 初始迁移把完整约束名再次交给命名约定，历史数据库中的实际名称因而带有双前缀。
    # 本次迁移同时统一为 ORM 模型使用的单前缀名称，后续迁移无需再猜测历史名称。
    # 这是历史初始迁移实际生成的约束名。若交给 ``drop_constraint``，项目的
    # 命名约定会再次追加 ``ck_search_runs_`` 前缀，导致 PostgreSQL 找不到约束。
    op.execute("ALTER TABLE search_runs DROP CONSTRAINT ck_search_runs_ck_search_runs_stage")
    op.create_check_constraint(
        op.f("ck_search_runs_stage"),
        "search_runs",
        "stage IN ('dispatch', 'provider_search', 'normalize', 'triage', "
        "'relevance_assessment', 'citation_enrichment', 'completed')",
    )
    op.execute(
        "COMMENT ON COLUMN search_runs.stage IS '处理阶段：dispatch 投递、"
        "provider_search 来源检索、normalize 规整、triage 初筛、"
        "relevance_assessment 相关性评估、citation_enrichment 题录补全、"
        "completed 完成'"
    )


def downgrade() -> None:
    """移除相关性评估阶段，回退前不应存在该阶段的运行。"""
    # 升级迁移创建的是单前缀名称；同样使用精确 DDL，避免回退时重复应用命名约定。
    op.execute("ALTER TABLE search_runs DROP CONSTRAINT ck_search_runs_stage")
    op.create_check_constraint(
        "ck_search_runs_stage",
        "search_runs",
        "stage IN ('dispatch', 'provider_search', 'normalize', 'triage', "
        "'citation_enrichment', 'completed')",
    )
    op.execute(
        "COMMENT ON COLUMN search_runs.stage IS '处理阶段：dispatch 投递、"
        "provider_search 来源检索、normalize 规整、triage 初筛、"
        "citation_enrichment 题录补全、completed 完成'"
    )
