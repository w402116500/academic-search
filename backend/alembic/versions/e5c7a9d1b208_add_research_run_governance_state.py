"""add research run governance state

Revision ID: e5c7a9d1b208
Revises: d4f8c2a9b715
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5c7a9d1b208"
down_revision: str | Sequence[str] | None = "d4f8c2a9b715"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存协作取消请求和公开阶段的可审计起始时间。"""
    op.add_column(
        "research_runs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "research_runs",
        sa.Column("stage_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("COMMENT ON COLUMN research_runs.cancel_requested_at IS '用户请求协作停止的时间'")
    op.execute("COMMENT ON COLUMN research_runs.stage_started_at IS '当前公开阶段开始时间'")


def downgrade() -> None:
    """移除 P2 增加的治理状态。"""
    op.drop_column("research_runs", "stage_started_at")
    op.drop_column("research_runs", "cancel_requested_at")
