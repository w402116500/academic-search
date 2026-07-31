"""add collection list index

Revision ID: b81e6f4a92d0
Revises: d7a4c9e2f18b
Create Date: 2026-07-31 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b81e6f4a92d0"
down_revision: str | Sequence[str] | None = "d7a4c9e2f18b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """加速按用户和状态读取最近更新工作区的列表页查询。"""
    op.create_index(
        "ix_research_collections_owner_status_updated_at",
        "research_collections",
        ["owner_user_id", "status", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    """删除本次新增的工作区列表组合索引。"""
    op.drop_index(
        "ix_research_collections_owner_status_updated_at",
        table_name="research_collections",
    )
