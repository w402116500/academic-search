"""align workflow unique constraints

Revision ID: f41c8e7b2a06
Revises: e2a7c4b9d113
Create Date: 2026-07-31 15:15:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f41c8e7b2a06"
down_revision: str | Sequence[str] | None = "e2a7c4b9d113"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """让数据库唯一性结构与 SQLAlchemy ``unique=True`` 模型声明一致。"""
    op.drop_index("uq_research_plans_arq_job_id", table_name="research_plans")
    op.create_unique_constraint(
        "uq_research_plans_arq_job_id",
        "research_plans",
        ["arq_job_id"],
    )

    op.drop_index("uq_search_runs_arq_job_id", table_name="search_runs")
    op.create_unique_constraint("uq_search_runs_arq_job_id", "search_runs", ["arq_job_id"])

    op.drop_index("uq_search_runs_redis_session_key", table_name="search_runs")
    op.create_unique_constraint(
        "uq_search_runs_redis_session_key",
        "search_runs",
        ["redis_session_key"],
    )


def downgrade() -> None:
    """恢复上一条迁移创建的唯一索引结构。"""
    op.drop_constraint("uq_search_runs_redis_session_key", "search_runs", type_="unique")
    op.create_index(
        "uq_search_runs_redis_session_key",
        "search_runs",
        ["redis_session_key"],
        unique=True,
    )

    op.drop_constraint("uq_search_runs_arq_job_id", "search_runs", type_="unique")
    op.create_index("uq_search_runs_arq_job_id", "search_runs", ["arq_job_id"], unique=True)

    op.drop_constraint("uq_research_plans_arq_job_id", "research_plans", type_="unique")
    op.create_index("uq_research_plans_arq_job_id", "research_plans", ["arq_job_id"], unique=True)
