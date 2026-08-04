"""add ingestion run submission time

Revision ID: a9f3c7d2e6b4
Revises: e5c7a9d1b208
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9f3c7d2e6b4"
down_revision: str | Sequence[str] | None = "e5c7a9d1b208"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """区分待确认入库记录与已实际提交给 Worker 的构建运行。"""
    op.add_column(
        "ingestion_runs",
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="确认构建后实际投递到 Worker 的时间",
        ),
    )
    op.create_index("ix_ingestion_runs_submitted_at", "ingestion_runs", ["submitted_at"])
    op.execute("COMMENT ON COLUMN ingestion_runs.created_at IS '运行记录创建时间'")


def downgrade() -> None:
    """移除实际提交时间和对应的配额查询索引。"""
    op.drop_index("ix_ingestion_runs_submitted_at", table_name="ingestion_runs")
    op.drop_column("ingestion_runs", "submitted_at")
    op.execute("COMMENT ON COLUMN ingestion_runs.created_at IS '任务投递时间'")
