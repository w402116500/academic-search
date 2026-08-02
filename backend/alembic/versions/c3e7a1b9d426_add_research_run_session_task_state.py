"""补齐研究运行的会话、队列任务与可恢复执行阶段。

Revision ID: c3e7a1b9d426
Revises: a6d2e9f7c418
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3e7a1b9d426"
down_revision = "a6d2e9f7c418"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """以可回填方式为已有研究运行补充会话和异步执行审计字段。"""
    op.add_column("research_runs", sa.Column("conversation_id", sa.UUID(), nullable=True))
    # 历史表理论上没有已执行运行；仍从输入消息回填，避免依赖这一假设。
    op.execute(
        "UPDATE research_runs AS run "
        "SET conversation_id = message.conversation_id "
        "FROM messages AS message "
        "WHERE message.id = run.input_message_id AND run.conversation_id IS NULL"
    )
    op.alter_column("research_runs", "conversation_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_research_runs_conversation_id_conversations"),
        "research_runs",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_research_runs_conversation_id"), "research_runs", ["conversation_id"])
    op.create_index(
        "ix_research_runs_conversation_created_at",
        "research_runs",
        ["conversation_id", "created_at"],
    )

    op.add_column("research_runs", sa.Column("arq_job_id", sa.String(length=128), nullable=True))
    op.create_unique_constraint(
        op.f("uq_research_runs_arq_job_id"), "research_runs", ["arq_job_id"]
    )

    op.add_column(
        "research_runs",
        sa.Column(
            "stage", sa.String(length=32), server_default=sa.text("'dispatch'"), nullable=False
        ),
    )
    op.create_index(op.f("ix_research_runs_stage"), "research_runs", ["stage"])
    op.create_check_constraint(
        op.f("ck_research_runs_stage"),
        "research_runs",
        "stage IN ('dispatch', 'preparing', 'hybrid_retrieval', 'parent_merging', "
        "'reranking', 'evidence_verifying', 'answering', 'completed', "
        "'awaiting_clarification', 'failed', 'cancelled')",
    )
    op.alter_column("research_runs", "stage", server_default=None)
    op.execute("COMMENT ON COLUMN research_runs.conversation_id IS '触发本次运行的研究会话标识'")
    op.execute("COMMENT ON COLUMN research_runs.arq_job_id IS 'Redis arq 研究任务标识'")
    op.execute(
        "COMMENT ON COLUMN research_runs.stage IS '可展示执行阶段：dispatch、检索、"
        "证据核验、回答或终态'"
    )
    op.create_index(
        "ix_messages_conversation_created_at",
        "messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    """移除本 revision 新增的运行恢复字段和索引。"""
    op.drop_index("ix_messages_conversation_created_at", table_name="messages")
    op.drop_constraint(op.f("ck_research_runs_stage"), "research_runs", type_="check")
    op.drop_index(op.f("ix_research_runs_stage"), table_name="research_runs")
    op.drop_column("research_runs", "stage")
    op.drop_constraint(op.f("uq_research_runs_arq_job_id"), "research_runs", type_="unique")
    op.drop_column("research_runs", "arq_job_id")
    op.drop_index("ix_research_runs_conversation_created_at", table_name="research_runs")
    op.drop_index(op.f("ix_research_runs_conversation_id"), table_name="research_runs")
    op.drop_constraint(
        op.f("fk_research_runs_conversation_id_conversations"),
        "research_runs",
        type_="foreignkey",
    )
    op.drop_column("research_runs", "conversation_id")
