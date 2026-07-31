"""add research workflow models

Revision ID: e2a7c4b9d113
Revises: b81e6f4a92d0
Create Date: 2026-07-31 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e2a7c4b9d113"
down_revision: str | Sequence[str] | None = "b81e6f4a92d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增研究阶段、可版本化计划和检索运行的持久化状态。"""
    # 既有工作区没有历史流程记录，因此安全地从 draft 开始；不会伪造检索完成状态。
    op.add_column(
        "research_collections",
        sa.Column(
            "workflow_stage",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'draft'"),
            comment="研究流程阶段：draft 草稿、analyzing 意图解析中、plan_review 计划待确认、"
            "retrieving 文献检索中、screening 候选筛选、collection_building 集合构建中、"
            "researching 可以证据研究、failed 执行失败",
        ),
    )
    op.create_check_constraint(
        "ck_research_collections_workflow_stage",
        "research_collections",
        "workflow_stage IN ('draft', 'analyzing', 'plan_review', 'retrieving', "
        "'screening', 'collection_building', 'researching', 'failed')",
    )
    op.create_index(
        "ix_research_collections_workflow_stage",
        "research_collections",
        ["workflow_stage"],
        unique=False,
    )

    op.create_table(
        "research_plans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="主键标识，由应用层生成 UUID",
        ),
        sa.Column(
            "collection_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="所属研究工作区标识",
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            comment="同一工作区内递增的研究计划版本号",
        ),
        sa.Column(
            "raw_request",
            sa.Text(),
            nullable=False,
            comment="用户提交的原始自然语言研究要求",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'generating'"),
            comment="计划状态：generating 生成中、ready 待确认、confirmed 已确认、"
            "failed 失败、superseded 已替代",
        ),
        sa.Column(
            "direction_options",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="模型生成的候选研究方向列表，包含标题、说明和子议题",
        ),
        sa.Column(
            "selected_direction_id",
            sa.String(length=64),
            nullable=True,
            comment="用户确认的候选方向标识，计划未确认时为空",
        ),
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="用户确认的时间范围、语言和固定准入范围",
        ),
        sa.Column(
            "query_plan",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="按文献来源拆分的检索表达式和过滤参数",
        ),
        sa.Column(
            "model_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="模型名称、提示词版本和结构化输出配置，不保存密钥",
        ),
        sa.Column(
            "arq_job_id",
            sa.String(length=128),
            nullable=True,
            comment="意图解析 arq 任务标识，计划未投递时为空",
        ),
        sa.Column(
            "error_code",
            sa.String(length=64),
            nullable=True,
            comment="计划生成失败的机器可识别错误码",
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment="计划生成失败的可展示说明",
        ),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="用户确认计划的时间",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="创建时间，统一保存为 UTC",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="最近一次通过 ORM 更新的时间，统一保存为 UTC",
        ),
        sa.CheckConstraint(
            "status IN ('generating', 'ready', 'confirmed', 'failed', 'superseded')",
            name="ck_research_plans_status",
        ),
        sa.CheckConstraint("revision > 0", name="ck_research_plans_revision_positive"),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["research_collections.id"],
            name="fk_research_plans_collection_id_research_collections",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_research_plans"),
        sa.UniqueConstraint(
            "collection_id",
            "revision",
            name="uq_research_plans_collection_revision",
        ),
        comment="用户研究要求解析后的可确认、可版本化研究计划",
    )
    op.create_index("ix_research_plans_collection_id", "research_plans", ["collection_id"])
    op.create_index("ix_research_plans_status", "research_plans", ["status"])
    op.create_index("uq_research_plans_arq_job_id", "research_plans", ["arq_job_id"], unique=True)
    op.create_index(
        "ix_research_plans_collection_status_updated_at",
        "research_plans",
        ["collection_id", "status", "updated_at"],
    )

    op.create_table(
        "search_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="主键标识，由应用层生成 UUID",
        ),
        sa.Column(
            "collection_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="所属研究工作区标识",
        ),
        sa.Column(
            "research_plan_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="本次检索使用的已确认研究计划标识",
        ),
        sa.Column(
            "arq_job_id",
            sa.String(length=128),
            nullable=True,
            comment="检索 arq 任务标识",
        ),
        sa.Column(
            "redis_session_key",
            sa.String(length=256),
            nullable=True,
            comment="候选和进度短期存储使用的 Redis 会话键",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'queued'"),
            comment="运行状态：queued 排队、running 运行、completed 完成、"
            "partial_failed 部分失败、failed 失败、cancelled 取消、expired 过期",
        ),
        sa.Column(
            "stage",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'dispatch'"),
            comment="处理阶段：dispatch 投递、provider_search 来源检索、normalize 规整、"
            "triage 初筛、citation_enrichment 题录补全、completed 完成",
        ),
        sa.Column(
            "attempt_no",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="本次运行的重试序号",
        ),
        sa.Column(
            "provider_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="各文献来源的状态、耗时、错误和返回数量摘要",
        ),
        sa.Column(
            "candidate_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="原始、去重、初筛和最终候选数量统计",
        ),
        sa.Column(
            "error_code",
            sa.String(length=64),
            nullable=True,
            comment="检索运行失败的机器可识别错误码",
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment="检索运行失败的可展示说明",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Worker 开始执行检索的时间",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="检索运行结束、失败或过期的时间",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="创建时间，统一保存为 UTC",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="最近一次通过 ORM 更新的时间，统一保存为 UTC",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial_failed', 'failed', "
            "'cancelled', 'expired')",
            name="ck_search_runs_status",
        ),
        sa.CheckConstraint(
            "stage IN ('dispatch', 'provider_search', 'normalize', 'triage', "
            "'citation_enrichment', 'completed')",
            name="ck_search_runs_stage",
        ),
        sa.CheckConstraint("attempt_no > 0", name="ck_search_runs_attempt_positive"),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["research_collections.id"],
            name="fk_search_runs_collection_id_research_collections",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["research_plan_id"],
            ["research_plans.id"],
            name="fk_search_runs_research_plan_id_research_plans",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_runs"),
        comment="一次多源文献检索的状态、统计和短期 Redis 会话引用",
    )
    op.create_index("ix_search_runs_collection_id", "search_runs", ["collection_id"])
    op.create_index("ix_search_runs_research_plan_id", "search_runs", ["research_plan_id"])
    op.create_index("ix_search_runs_status", "search_runs", ["status"])
    op.create_index("ix_search_runs_stage", "search_runs", ["stage"])
    op.create_index("uq_search_runs_arq_job_id", "search_runs", ["arq_job_id"], unique=True)
    op.create_index(
        "uq_search_runs_redis_session_key",
        "search_runs",
        ["redis_session_key"],
        unique=True,
    )
    op.create_index(
        "uq_search_runs_active_plan",
        "search_runs",
        ["research_plan_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_search_runs_collection_status_created_at",
        "search_runs",
        ["collection_id", "status", "created_at"],
    )


def downgrade() -> None:
    """按依赖顺序删除工作流表和工作区阶段字段。"""
    op.drop_index("ix_search_runs_collection_status_created_at", table_name="search_runs")
    op.drop_index("uq_search_runs_active_plan", table_name="search_runs")
    op.drop_index("uq_search_runs_redis_session_key", table_name="search_runs")
    op.drop_index("uq_search_runs_arq_job_id", table_name="search_runs")
    op.drop_index("ix_search_runs_stage", table_name="search_runs")
    op.drop_index("ix_search_runs_status", table_name="search_runs")
    op.drop_index("ix_search_runs_research_plan_id", table_name="search_runs")
    op.drop_index("ix_search_runs_collection_id", table_name="search_runs")
    op.drop_table("search_runs")

    op.drop_index("ix_research_plans_collection_status_updated_at", table_name="research_plans")
    op.drop_index("uq_research_plans_arq_job_id", table_name="research_plans")
    op.drop_index("ix_research_plans_status", table_name="research_plans")
    op.drop_index("ix_research_plans_collection_id", table_name="research_plans")
    op.drop_table("research_plans")

    op.drop_index("ix_research_collections_workflow_stage", table_name="research_collections")
    op.drop_constraint(
        "ck_research_collections_workflow_stage",
        "research_collections",
        type_="check",
    )
    op.drop_column("research_collections", "workflow_stage")
