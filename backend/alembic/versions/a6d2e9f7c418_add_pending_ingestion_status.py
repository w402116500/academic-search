"""add pending ingestion status

Revision ID: a6d2e9f7c418
Revises: f41c8e7b2a06
Create Date: 2026-08-01 03:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6d2e9f7c418"
down_revision: str | Sequence[str] | None = "f41c8e7b2a06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """允许待确认文献拥有入库运行，但不允许 Worker 在确认前领取。"""
    # 这是历史迁移已固化的最终约束名；op.f() 防止命名约定再次添加 ck_ 前缀。
    op.drop_constraint(op.f("ck_ingestion_runs_status"), "ingestion_runs", type_="check")
    op.create_check_constraint(
        op.f("ck_ingestion_runs_status"),
        "ingestion_runs",
        "status IN ('pending', 'queued', 'running', 'completed', 'failed', 'cancelled')",
    )
    op.alter_column(
        "ingestion_runs",
        "status",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        existing_comment="运行状态",
        comment="运行状态：pending 待确认、queued 已投递、running 执行中、"
        "completed 完成、failed 失败、cancelled 已取消",
    )


def downgrade() -> None:
    """回退时将待确认记录转为历史上等价的排队状态。"""
    op.execute("UPDATE ingestion_runs SET status = 'queued' WHERE status = 'pending'")
    op.drop_constraint(op.f("ck_ingestion_runs_status"), "ingestion_runs", type_="check")
    op.create_check_constraint(
        op.f("ck_ingestion_runs_status"),
        "ingestion_runs",
        "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
    )
    op.alter_column(
        "ingestion_runs",
        "status",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        existing_comment="运行状态：pending 待确认、queued 已投递、running 执行中、"
        "completed 完成、failed 失败、cancelled 已取消",
        comment="运行状态",
    )
