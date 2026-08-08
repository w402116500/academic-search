"""add search candidate review tables

Revision ID: b9c2d7e8f104
Revises: 0f4c1e2a9b87
Create Date: 2026-08-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b9c2d7e8f104"
down_revision: str | Sequence[str] | None = "0f4c1e2a9b87"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 Search 边界内的候选审核与全文准备持久表。"""
    op.create_table(
        "search_run_candidates",
        sa.Column("search_run_id", sa.UUID(), nullable=False, comment="所属检索运行标识"),
        sa.Column("candidate_id", sa.UUID(), nullable=False, comment="检索运行内稳定候选标识"),
        sa.Column(
            "position", sa.Integer(), nullable=False, comment="候选在本次检索归并结果中的稳定顺序"
        ),
        sa.Column("doi", sa.String(length=512), nullable=True, comment="候选 DOI"),
        sa.Column("title", sa.Text(), nullable=False, comment="候选标题"),
        sa.Column("title_key", sa.Text(), nullable=False, comment="候选去重标题键"),
        sa.Column(
            "language",
            sa.String(length=16),
            server_default=sa.text("'unknown'"),
            nullable=False,
            comment="候选主语言：zh、en、other 或 unknown",
        ),
        sa.Column(
            "authors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="候选作者展示投影",
        ),
        sa.Column("abstract", sa.Text(), nullable=True, comment="候选摘要"),
        sa.Column("published_year", sa.SmallInteger(), nullable=True, comment="候选发表年份"),
        sa.Column(
            "published_date",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="候选发表日期结构化投影",
        ),
        sa.Column("venue", sa.String(length=500), nullable=True, comment="期刊、会议或平台"),
        sa.Column("document_type", sa.String(length=128), nullable=True, comment="来源文献类型"),
        sa.Column("volume", sa.String(length=128), nullable=True, comment="卷号"),
        sa.Column("issue", sa.String(length=128), nullable=True, comment="期号"),
        sa.Column("pages", sa.String(length=128), nullable=True, comment="页码"),
        sa.Column("article_number", sa.String(length=128), nullable=True, comment="文章编号"),
        sa.Column("publisher", sa.String(length=500), nullable=True, comment="出版方"),
        sa.Column(
            "citation_counts_by_source",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="各来源可展示引用计数",
        ),
        sa.Column(
            "links",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="候选页面、开放获取和全文链接投影",
        ),
        sa.Column(
            "is_open_access",
            sa.Boolean(),
            nullable=True,
            comment="来源声明或合并后判断的开放获取状态",
        ),
        sa.Column(
            "source_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="最小来源引用，不保存完整 provider 原始响应",
        ),
        sa.Column(
            "triage", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="基础初筛结果"
        ),
        sa.Column(
            "relevance_state",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
            comment="相关性处理状态",
        ),
        sa.Column(
            "relevance_assessment",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="已通过证据校验的相关性评估",
        ),
        sa.Column(
            "relevance_error",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="相关性终态失败摘要",
        ),
        sa.Column(
            "citation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="候选题录补全投影",
        ),
        sa.Column(
            "pdf_availability",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="公开 PDF 可行动状态",
        ),
        sa.Column(
            "relevance_retry_attempt_no",
            sa.Integer(),
            nullable=True,
            comment="下一次相关性重试任务应处理该候选的尝试序号",
        ),
        sa.Column(
            "selected_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="用户加入准备清单的时间；为空表示未选择",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="创建时间，统一保存为 UTC",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="最近一次通过 ORM 更新的时间，统一保存为 UTC",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(authors) = 'array'",
            name=op.f("ck_search_run_candidates_authors"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(citation_counts_by_source) = 'object'",
            name=op.f("ck_search_run_candidates_citation_counts"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(links) = 'object'",
            name=op.f("ck_search_run_candidates_links"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_refs) = 'array'",
            name=op.f("ck_search_run_candidates_source_refs"),
        ),
        sa.CheckConstraint(
            "language IN ('zh', 'en', 'other', 'unknown')",
            name=op.f("ck_search_run_candidates_language"),
        ),
        sa.CheckConstraint(
            "position >= 0",
            name=op.f("ck_search_run_candidates_position_non_negative"),
        ),
        sa.CheckConstraint(
            "relevance_state IN ('pending', 'completed', 'excluded', 'failed', 'skipped')",
            name=op.f("ck_search_run_candidates_relevance_state"),
        ),
        sa.ForeignKeyConstraint(
            ["search_run_id"],
            ["search_runs.id"],
            name=op.f("fk_search_run_candidates_search_run_id_search_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "search_run_id",
            "candidate_id",
            name="search_run_candidate_identity",
        ),
        comment="Search 边界内可恢复的候选审核事实，不等同于长期论文事实",
    )
    op.create_index(
        op.f("ix_search_run_candidates_doi"),
        "search_run_candidates",
        ["doi"],
        unique=False,
    )
    op.create_index(
        op.f("ix_search_run_candidates_language"),
        "search_run_candidates",
        ["language"],
        unique=False,
    )
    op.create_index(
        op.f("ix_search_run_candidates_published_year"),
        "search_run_candidates",
        ["published_year"],
        unique=False,
    )
    op.create_index(
        op.f("ix_search_run_candidates_relevance_state"),
        "search_run_candidates",
        ["relevance_state"],
        unique=False,
    )
    op.create_index(
        op.f("ix_search_run_candidates_selected_at"),
        "search_run_candidates",
        ["selected_at"],
        unique=False,
    )
    op.create_index(
        "ix_search_run_candidates_run_position",
        "search_run_candidates",
        ["search_run_id", "position"],
        unique=False,
    )
    op.create_index(
        "ix_search_run_candidates_run_relevance_state",
        "search_run_candidates",
        ["search_run_id", "relevance_state"],
        unique=False,
    )
    op.create_index(
        "ix_search_run_candidates_run_selected_at",
        "search_run_candidates",
        ["search_run_id", "selected_at"],
        unique=False,
    )

    op.create_table(
        "search_candidate_fulltext_states",
        sa.Column("search_run_id", sa.UUID(), nullable=False, comment="所属检索运行标识"),
        sa.Column("candidate_id", sa.UUID(), nullable=False, comment="所属候选标识"),
        sa.Column("attempt_no", sa.Integer(), nullable=False, comment="全文任务尝试序号"),
        sa.Column("status", sa.String(length=32), nullable=False, comment="全文任务状态"),
        sa.Column(
            "candidate",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="全文边界所需的候选投影",
        ),
        sa.Column(
            "result_document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="已校验暂存全文文件投影",
        ),
        sa.Column(
            "result_error",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="终态失败的安全错误摘要",
        ),
        sa.Column(
            "arq_job_id", sa.String(length=128), nullable=True, comment="候选全文 arq 任务标识"
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="本轮全文准备请求时间",
        ),
        sa.Column(
            "state_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="全文状态业务更新时间",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="创建时间，统一保存为 UTC",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="最近一次通过 ORM 更新的时间，统一保存为 UTC",
        ),
        sa.CheckConstraint(
            "attempt_no > 0",
            name=op.f("ck_search_candidate_fulltext_states_attempt_positive"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(candidate) = 'object'",
            name=op.f("ck_search_candidate_fulltext_states_candidate"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'downloading', 'validating', 'available', "
            "'requires_upload', 'rejected', 'failed')",
            name=op.f("ck_search_candidate_fulltext_states_status"),
        ),
        sa.ForeignKeyConstraint(
            ["search_run_id", "candidate_id"],
            ["search_run_candidates.search_run_id", "search_run_candidates.candidate_id"],
            name=op.f("fk_search_candidate_fulltext_states_search_run_id_search_run_candidates"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "search_run_id",
            "candidate_id",
            name="search_candidate_fulltext_state_identity",
        ),
        comment="Search 候选进入全文准备阶段后的可恢复状态",
    )
    op.create_index(
        op.f("ix_search_candidate_fulltext_states_status"),
        "search_candidate_fulltext_states",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_search_candidate_fulltext_states_run_status",
        "search_candidate_fulltext_states",
        ["search_run_id", "status"],
        unique=False,
    )

    op.alter_column(
        "search_runs",
        "redis_session_key",
        existing_type=sa.String(length=256),
        nullable=True,
        comment="进度事件、锁和可丢缓存使用的 Redis 会话键",
        existing_comment="候选和进度短期存储使用的 Redis 会话键",
    )
    op.alter_column(
        "collection_bibliography_entries",
        "source_candidate_id",
        existing_type=sa.UUID(),
        nullable=True,
        comment="来源检索候选标识；不作为全局论文事实",
        existing_comment="来源 Redis 候选标识；不作为全局论文事实",
    )


def downgrade() -> None:
    """删除候选审核持久表，恢复旧注释。"""
    op.alter_column(
        "search_runs",
        "redis_session_key",
        existing_type=sa.String(length=256),
        nullable=True,
        comment="候选和进度短期存储使用的 Redis 会话键",
        existing_comment="进度事件、锁和可丢缓存使用的 Redis 会话键",
    )
    op.alter_column(
        "collection_bibliography_entries",
        "source_candidate_id",
        existing_type=sa.UUID(),
        nullable=True,
        comment="来源 Redis 候选标识；不作为全局论文事实",
        existing_comment="来源检索候选标识；不作为全局论文事实",
    )
    op.drop_index(
        "ix_search_candidate_fulltext_states_run_status",
        table_name="search_candidate_fulltext_states",
    )
    op.drop_index(
        op.f("ix_search_candidate_fulltext_states_status"),
        table_name="search_candidate_fulltext_states",
    )
    op.drop_table("search_candidate_fulltext_states")
    op.drop_index("ix_search_run_candidates_run_selected_at", table_name="search_run_candidates")
    op.drop_index(
        "ix_search_run_candidates_run_relevance_state", table_name="search_run_candidates"
    )
    op.drop_index("ix_search_run_candidates_run_position", table_name="search_run_candidates")
    op.drop_index(op.f("ix_search_run_candidates_selected_at"), table_name="search_run_candidates")
    op.drop_index(
        op.f("ix_search_run_candidates_relevance_state"),
        table_name="search_run_candidates",
    )
    op.drop_index(
        op.f("ix_search_run_candidates_published_year"),
        table_name="search_run_candidates",
    )
    op.drop_index(op.f("ix_search_run_candidates_language"), table_name="search_run_candidates")
    op.drop_index(op.f("ix_search_run_candidates_doi"), table_name="search_run_candidates")
    op.drop_table("search_run_candidates")
