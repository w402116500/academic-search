"""add collection bibliography entries

Revision ID: 0f4c1e2a9b87
Revises: a9f3c7d2e6b4, c8d1f2a3b4e5
Create Date: 2026-08-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0f4c1e2a9b87"
down_revision: str | Sequence[str] | None = ("a9f3c7d2e6b4", "c8d1f2a3b4e5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建集合书目条目表，并把历史已核验集合论文回填为条目。"""
    op.create_table(
        "collection_bibliography_entries",
        sa.Column("collection_id", sa.UUID(), nullable=False, comment="所属研究工作区标识"),
        sa.Column(
            "source_search_run_id",
            sa.UUID(),
            nullable=True,
            comment="来源检索运行标识；历史或手动条目可以为空",
        ),
        sa.Column(
            "source_candidate_id",
            sa.UUID(),
            nullable=True,
            comment="来源 Redis 候选标识；不作为全局论文事实",
        ),
        sa.Column(
            "paper_id",
            sa.UUID(),
            nullable=True,
            comment="已核验共享论文标识；题录不可用时为空",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
            comment="集合内书目状态：active 或 archived",
        ),
        sa.Column("candidate_title", sa.Text(), nullable=False, comment="候选标题快照"),
        sa.Column(
            "candidate_authors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="候选作者展示快照",
        ),
        sa.Column("candidate_abstract", sa.Text(), nullable=True, comment="候选摘要快照"),
        sa.Column(
            "candidate_publication_year",
            sa.SmallInteger(),
            nullable=True,
            comment="候选发表年份快照",
        ),
        sa.Column(
            "candidate_venue",
            sa.String(length=500),
            nullable=True,
            comment="候选来源期刊、会议或平台快照",
        ),
        sa.Column(
            "candidate_doi", sa.String(length=512), nullable=True, comment="候选 DOI 展示快照"
        ),
        sa.Column(
            "candidate_source_url",
            sa.Text(),
            nullable=True,
            comment="候选来源页面或公开地址快照",
        ),
        sa.Column(
            "source_record",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="来源原始记录中允许持久保存的结构化快照",
        ),
        sa.Column(
            "citation_status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
            comment="题录核验状态：pending、ready 或 unavailable",
        ),
        sa.Column("citation_text", sa.Text(), nullable=True, comment="已核验时生成的正式引用文本"),
        sa.Column(
            "citation_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="已核验题录或稳定失败状态的结构化快照",
        ),
        sa.Column(
            "pdf_status",
            sa.String(length=32),
            server_default=sa.text("'unknown'"),
            nullable=False,
            comment="公开 PDF 探测状态：unknown、available 或 requires_upload",
        ),
        sa.Column(
            "pdf_source_url",
            sa.Text(),
            nullable=True,
            comment="已探测可自动获取 PDF 的安全来源地址",
        ),
        sa.Column(
            "pdf_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="PDF 可得性探测的结构化快照",
        ),
        sa.Column(
            "content_status",
            sa.String(length=32),
            server_default=sa.text("'requires_upload'"),
            nullable=False,
            comment="内容处理状态，用于区分需上传、自动获取、入库中和已可研究",
        ),
        sa.Column(
            "automatic_download_attempts",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
            comment="系统自动下载 PDF 的已尝试次数，最多两次",
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
            comment="用户在当前工作区添加的标签列表",
        ),
        sa.Column("note", sa.Text(), nullable=True, comment="用户对该书目的笔记"),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="加入当前工作区的时间",
        ),
        sa.Column("id", sa.UUID(), nullable=False, comment="主键标识，由应用层生成 UUID"),
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
            "automatic_download_attempts BETWEEN 0 AND 2",
            name=op.f("ck_collection_bibliography_entries_automatic_download_attempts_range"),
        ),
        sa.CheckConstraint(
            "citation_status IN ('pending', 'ready', 'unavailable')",
            name=op.f("ck_collection_bibliography_entries_citation_status"),
        ),
        sa.CheckConstraint(
            "citation_text IS NULL OR citation_status = 'ready'",
            name=op.f("ck_collection_bibliography_entries_citation_text_requires_ready"),
        ),
        sa.CheckConstraint(
            "content_status IN ('pending_auto_download', 'requires_upload', "
            "'document_ready', 'ingesting', 'researchable', 'failed', 'cancelled')",
            name=op.f("ck_collection_bibliography_entries_content_status"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(candidate_authors) = 'array'",
            name=op.f("ck_collection_bibliography_entries_candidate_authors"),
        ),
        sa.CheckConstraint(
            "pdf_status IN ('unknown', 'available', 'requires_upload')",
            name=op.f("ck_collection_bibliography_entries_pdf_status"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name=op.f("ck_collection_bibliography_entries_status"),
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["research_collections.id"],
            name=op.f("fk_collection_bibliography_entries_collection_id_research_collections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"],
            ["papers.id"],
            name=op.f("fk_collection_bibliography_entries_paper_id_papers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_search_run_id"],
            ["search_runs.id"],
            name=op.f("fk_collection_bibliography_entries_source_search_run_id_search_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection_bibliography_entries")),
        sa.UniqueConstraint(
            "collection_id",
            "id",
            name="collection_bibliography_entry_id",
        ),
        sa.UniqueConstraint(
            "collection_id",
            "source_search_run_id",
            "source_candidate_id",
            name="collection_source_candidate",
        ),
        comment="研究集合中用户保留的候选书目快照",
    )
    _create_entry_indexes()
    op.add_column(
        "documents",
        sa.Column(
            "bibliography_entry_id", sa.UUID(), nullable=True, comment="所属集合书目条目标识"
        ),
    )
    op.create_index(
        op.f("ix_documents_bibliography_entry_id"),
        "documents",
        ["bibliography_entry_id"],
        unique=False,
    )

    _backfill_entries_from_collection_papers()

    op.alter_column(
        "documents",
        "bibliography_entry_id",
        existing_type=sa.UUID(),
        nullable=False,
        existing_comment="所属集合书目条目标识",
    )
    op.drop_constraint(
        op.f("fk_documents_collection_id_collection_papers"),
        "documents",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_documents_paper_id_papers"),
        "documents",
        "papers",
        ["paper_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_documents_collection_id_collection_bibliography_entries"),
        "documents",
        "collection_bibliography_entries",
        ["collection_id", "bibliography_entry_id"],
        ["collection_id", "id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "documents",
        "paper_id",
        existing_type=sa.UUID(),
        nullable=True,
        comment="对应已验证论文标识；题录不可用时为空",
        existing_comment="对应已验证论文标识",
    )
    op.create_table_comment(
        "documents",
        "研究集合书目条目取得的、可进入 RAG 入库链路的文件",
        existing_comment="研究工作区内可用于 RAG 的论文文件",
    )
    op.alter_column(
        "ingestion_runs",
        "cancel_requested_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
        comment="工作区删除等系统操作请求协作停止的时间",
        existing_comment=None,
    )


def downgrade() -> None:
    """恢复以已核验论文关联作为文档所有权边界的旧结构。"""
    connection = op.get_bind()
    _restore_collection_papers_from_entries(connection)
    paperless_documents = connection.scalar(
        sa.text("SELECT count(*) FROM documents WHERE paper_id IS NULL")
    )
    if paperless_documents:
        raise RuntimeError("无法降级：存在没有已核验 Paper 的文档，不能伪造 Paper 行。")

    op.alter_column(
        "ingestion_runs",
        "cancel_requested_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
        comment=None,
        existing_comment="工作区删除等系统操作请求协作停止的时间",
    )
    op.create_table_comment(
        "documents",
        "研究工作区内可用于 RAG 的论文文件",
        existing_comment="研究集合书目条目取得的、可进入 RAG 入库链路的文件",
    )
    op.drop_constraint(
        op.f("fk_documents_collection_id_collection_bibliography_entries"),
        "documents",
        type_="foreignkey",
    )
    op.drop_constraint(op.f("fk_documents_paper_id_papers"), "documents", type_="foreignkey")
    op.alter_column(
        "documents",
        "paper_id",
        existing_type=sa.UUID(),
        nullable=False,
        comment="对应已验证论文标识",
        existing_comment="对应已验证论文标识；题录不可用时为空",
    )
    op.create_foreign_key(
        op.f("fk_documents_collection_id_collection_papers"),
        "documents",
        "collection_papers",
        ["collection_id", "paper_id"],
        ["collection_id", "paper_id"],
        ondelete="CASCADE",
    )
    op.drop_index(op.f("ix_documents_bibliography_entry_id"), table_name="documents")
    op.drop_column("documents", "bibliography_entry_id")
    _drop_entry_indexes()
    op.drop_table("collection_bibliography_entries")


def _create_entry_indexes() -> None:
    op.create_index(
        op.f("ix_collection_bibliography_entries_candidate_doi"),
        "collection_bibliography_entries",
        ["candidate_doi"],
        unique=False,
    )
    op.create_index(
        op.f("ix_collection_bibliography_entries_candidate_publication_year"),
        "collection_bibliography_entries",
        ["candidate_publication_year"],
        unique=False,
    )
    op.create_index(
        "ix_collection_bibliography_entries_collection_status_added_at",
        "collection_bibliography_entries",
        ["collection_id", "status", "added_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_collection_bibliography_entries_collection_id"),
        "collection_bibliography_entries",
        ["collection_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_collection_bibliography_entries_content_status"),
        "collection_bibliography_entries",
        ["content_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_collection_bibliography_entries_citation_status"),
        "collection_bibliography_entries",
        ["citation_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_collection_bibliography_entries_paper_id"),
        "collection_bibliography_entries",
        ["paper_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_collection_bibliography_entries_pdf_status"),
        "collection_bibliography_entries",
        ["pdf_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_collection_bibliography_entries_source_candidate_id"),
        "collection_bibliography_entries",
        ["source_candidate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_collection_bibliography_entries_source_search_run_id"),
        "collection_bibliography_entries",
        ["source_search_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_collection_bibliography_entries_status"),
        "collection_bibliography_entries",
        ["status"],
        unique=False,
    )


def _drop_entry_indexes() -> None:
    op.drop_index(
        op.f("ix_collection_bibliography_entries_status"),
        table_name="collection_bibliography_entries",
    )
    op.drop_index(
        op.f("ix_collection_bibliography_entries_source_search_run_id"),
        table_name="collection_bibliography_entries",
    )
    op.drop_index(
        op.f("ix_collection_bibliography_entries_source_candidate_id"),
        table_name="collection_bibliography_entries",
    )
    op.drop_index(
        op.f("ix_collection_bibliography_entries_pdf_status"),
        table_name="collection_bibliography_entries",
    )
    op.drop_index(
        op.f("ix_collection_bibliography_entries_paper_id"),
        table_name="collection_bibliography_entries",
    )
    op.drop_index(
        op.f("ix_collection_bibliography_entries_citation_status"),
        table_name="collection_bibliography_entries",
    )
    op.drop_index(
        op.f("ix_collection_bibliography_entries_content_status"),
        table_name="collection_bibliography_entries",
    )
    op.drop_index(
        op.f("ix_collection_bibliography_entries_collection_id"),
        table_name="collection_bibliography_entries",
    )
    op.drop_index(
        "ix_collection_bibliography_entries_collection_status_added_at",
        table_name="collection_bibliography_entries",
    )
    op.drop_index(
        op.f("ix_collection_bibliography_entries_candidate_publication_year"),
        table_name="collection_bibliography_entries",
    )
    op.drop_index(
        op.f("ix_collection_bibliography_entries_candidate_doi"),
        table_name="collection_bibliography_entries",
    )


def _backfill_entries_from_collection_papers() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT
                cp.collection_id,
                cp.paper_id,
                cp.status,
                cp.tags,
                cp.note,
                cp.added_at,
                p.title,
                p.authors,
                p.abstract,
                p.publication_year,
                p.venue,
                p.doi,
                p.official_url,
                p.citation_text,
                p.citation_provider,
                p.citation_source_url,
                p.citation_verified_at
            FROM collection_papers AS cp
            JOIN papers AS p ON p.id = cp.paper_id
            """
        )
    ).mappings()
    for row in rows:
        entry_id = uuid4()
        insert_entry = sa.text(
            """
                INSERT INTO collection_bibliography_entries (
                    id,
                    collection_id,
                    paper_id,
                    status,
                    candidate_title,
                    candidate_authors,
                    candidate_abstract,
                    candidate_publication_year,
                    candidate_venue,
                    candidate_doi,
                    candidate_source_url,
                    source_record,
                    citation_status,
                    citation_text,
                    citation_snapshot,
                    pdf_status,
                    pdf_snapshot,
                    content_status,
                    tags,
                    note,
                    added_at
                )
                VALUES (
                    :id,
                    :collection_id,
                    :paper_id,
                    :status,
                    :candidate_title,
                    :candidate_authors,
                    :candidate_abstract,
                    :candidate_publication_year,
                    :candidate_venue,
                    :candidate_doi,
                    :candidate_source_url,
                    :source_record,
                    'ready',
                    :citation_text,
                    :citation_snapshot,
                    'unknown',
                    :pdf_snapshot,
                    'document_ready',
                    :tags,
                    :note,
                    :added_at
                )
                """
        ).bindparams(
            sa.bindparam("candidate_authors", type_=postgresql.JSONB),
            sa.bindparam("source_record", type_=postgresql.JSONB),
            sa.bindparam("citation_snapshot", type_=postgresql.JSONB),
            sa.bindparam("pdf_snapshot", type_=postgresql.JSONB),
            sa.bindparam("tags", type_=postgresql.ARRAY(sa.Text())),
        )
        connection.execute(
            insert_entry,
            {
                "id": entry_id,
                "collection_id": row["collection_id"],
                "paper_id": row["paper_id"],
                "status": row["status"],
                "candidate_title": row["title"],
                "candidate_authors": row["authors"],
                "candidate_abstract": row["abstract"],
                "candidate_publication_year": row["publication_year"],
                "candidate_venue": row["venue"],
                "candidate_doi": row["doi"],
                "candidate_source_url": row["official_url"],
                "source_record": {"backfilled_from": "collection_papers"},
                "citation_text": row["citation_text"],
                "citation_snapshot": {
                    "provider": row["citation_provider"],
                    "source_url": row["citation_source_url"],
                    "verified_at": _isoformat(row["citation_verified_at"]),
                },
                "pdf_snapshot": {"backfilled_from": "documents"},
                "tags": row["tags"],
                "note": row["note"],
                "added_at": row["added_at"],
            },
        )
        connection.execute(
            sa.text(
                """
                UPDATE documents
                SET bibliography_entry_id = :entry_id
                WHERE collection_id = :collection_id AND paper_id = :paper_id
                """
            ),
            {
                "entry_id": entry_id,
                "collection_id": row["collection_id"],
                "paper_id": row["paper_id"],
            },
        )


def _restore_collection_papers_from_entries(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO collection_papers (collection_id, paper_id, status, tags, note, added_at)
            SELECT collection_id, paper_id, status, tags, note, added_at
            FROM collection_bibliography_entries
            WHERE paper_id IS NOT NULL
            ON CONFLICT (collection_id, paper_id) DO NOTHING
            """
        )
    )


def _isoformat(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
