"""add workspace deletion fence

Revision ID: c8d1f2a3b4e5
Revises: f41c8e7b2a06
Create Date: 2026-08-07 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d1f2a3b4e5"
down_revision: str | Sequence[str] | None = "f41c8e7b2a06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加删除中的持久围栏与入库协作取消标记。"""
    op.drop_constraint("status", "research_collections", type_="check")
    op.create_check_constraint(
        "status",
        "research_collections",
        "status IN ('active', 'archived', 'deleted', 'deleting')",
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """移除永久删除所需的内部状态字段。"""
    op.drop_column("ingestion_runs", "cancel_requested_at")
    op.drop_constraint("status", "research_collections", type_="check")
    op.create_check_constraint(
        "status",
        "research_collections",
        "status IN ('active', 'archived', 'deleted')",
    )
