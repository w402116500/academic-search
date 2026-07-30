"""align research document admission

Revision ID: d7a4c9e2f18b
Revises: 5f4b9012d55c
Create Date: 2026-07-30 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d7a4c9e2f18b"
down_revision: str | Sequence[str] | None = "5f4b9012d55c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PAPER_TYPE_VALUES = (
    "'journal_article'",
    "'conference_paper'",
    "'book'",
    "'book_chapter'",
    "'dissertation'",
    "'preprint'",
    "'posted_content'",
    "'dataset'",
    "'editorial'",
    "'correction'",
    "'grant'",
    "'peer_review'",
    "'reference_entry'",
    "'retraction'",
    "'other'",
)
_PAPER_TYPE_CHECK = f"paper_type IS NULL OR paper_type IN ({', '.join(_PAPER_TYPE_VALUES)})"


def upgrade() -> None:
    """使长期文献、全文来源与 RAG 版本约束符合严格准入规则。"""
    # 现有数据若含空 DOI，应由维护者先明确处理；迁移不删除或伪造这些旧数据。
    op.drop_index("uq_papers_doi", table_name="papers")
    op.alter_column(
        "papers",
        "doi",
        existing_type=sa.VARCHAR(length=512),
        nullable=False,
        comment="规范化且唯一的 DOI",
        existing_comment="规范化 DOI，可为空",
    )
    op.create_index("uq_papers_doi", "papers", ["doi"], unique=True)
    op.drop_constraint(op.f("uq_papers_bibliographic_fingerprint"), "papers", type_="unique")
    op.drop_column("papers", "bibliographic_fingerprint")

    op.add_column(
        "papers",
        sa.Column(
            "publication_month",
            sa.SmallInteger(),
            nullable=True,
            comment="发表月份，来自格式中立题录",
        ),
    )
    op.add_column(
        "papers",
        sa.Column(
            "publication_day",
            sa.SmallInteger(),
            nullable=True,
            comment="发表日期，必须与月份同时存在",
        ),
    )
    op.add_column(
        "papers", sa.Column("volume", sa.String(length=128), nullable=True, comment="卷号")
    )
    op.add_column(
        "papers", sa.Column("issue", sa.String(length=128), nullable=True, comment="期号")
    )
    op.add_column(
        "papers", sa.Column("pages", sa.String(length=128), nullable=True, comment="页码范围")
    )
    op.add_column(
        "papers",
        sa.Column(
            "article_number", sa.String(length=128), nullable=True, comment="文章号；与页码分别保留"
        ),
    )
    op.add_column(
        "papers", sa.Column("publisher", sa.String(length=500), nullable=True, comment="出版者")
    )
    op.create_check_constraint(
        op.f("ck_papers_publication_month_range"),
        "papers",
        "publication_month IS NULL OR publication_month BETWEEN 1 AND 12",
    )
    op.create_check_constraint(
        op.f("ck_papers_publication_day_range"),
        "papers",
        "publication_day IS NULL OR publication_day BETWEEN 1 AND 31",
    )
    op.create_check_constraint(
        op.f("ck_papers_publication_day_requires_month"),
        "papers",
        "publication_day IS NULL OR publication_month IS NOT NULL",
    )
    op.create_check_constraint(op.f("ck_papers_paper_type"), "papers", _PAPER_TYPE_CHECK)
    op.alter_column(
        "papers",
        "authors",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment="有序作者数组；每项为 literal 或 given 与 family 的结构化姓名",
        existing_comment="有序作者数组，来自结构化书目元数据",
        existing_nullable=False,
    )
    op.alter_column(
        "papers",
        "paper_type",
        existing_type=sa.VARCHAR(length=64),
        comment="规整文献类型，如 journal_article 或 preprint",
        existing_comment="文献类型，如 journal-article 或 preprint",
        existing_nullable=True,
    )
    op.alter_column(
        "papers",
        "citation_text",
        existing_type=sa.TEXT(),
        comment="默认展示的 GB/T 7714-2015 引文缓存",
        existing_comment="已核验的 GB/T 7714-2015 引文文本",
        existing_nullable=False,
    )
    op.alter_column(
        "papers",
        "citation_verified_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        comment="DOI 题录核验完成的时间",
        existing_comment="标题、年份和引文完成核验的时间",
        existing_nullable=False,
        existing_server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    op.create_table_comment(
        "papers",
        "已核验 DOI 且已取得可处理正文的研究文献书目信息",
        existing_comment="已通过正式引文核验的论文书目信息",
    )

    op.drop_constraint(op.f("ck_documents_origin_kind"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_origin_kind"),
        "documents",
        "origin_kind IN ('user_upload', 'open_access', 'official_download')",
    )
    op.alter_column(
        "documents",
        "origin_kind",
        existing_type=sa.VARCHAR(length=32),
        comment="文件取得方式：user_upload、open_access 或 official_download",
        existing_comment="文件取得方式：user_upload 或 open_access",
        existing_nullable=False,
    )

    op.add_column(
        "ingestion_runs",
        sa.Column(
            "is_current",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否为当前可参与 RAG 检索的已完成版本",
        ),
    )
    op.create_check_constraint(
        op.f("ck_ingestion_runs_current_requires_completed"),
        "ingestion_runs",
        "NOT is_current OR status = 'completed'",
    )
    op.create_index(
        "uq_ingestion_runs_current_document",
        "ingestion_runs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )


def downgrade() -> None:
    """恢复严格准入规则实施前的数据库结构。"""
    op.drop_index("uq_ingestion_runs_current_document", table_name="ingestion_runs")
    op.drop_constraint(
        op.f("ck_ingestion_runs_current_requires_completed"), "ingestion_runs", type_="check"
    )
    op.drop_column("ingestion_runs", "is_current")

    op.alter_column(
        "documents",
        "origin_kind",
        existing_type=sa.VARCHAR(length=32),
        comment="文件取得方式：user_upload 或 open_access",
        existing_comment="文件取得方式：user_upload、open_access 或 official_download",
        existing_nullable=False,
    )
    op.drop_constraint(op.f("ck_documents_origin_kind"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_origin_kind"),
        "documents",
        "origin_kind IN ('user_upload', 'open_access')",
    )

    op.create_table_comment(
        "papers",
        "已通过正式引文核验的论文书目信息",
        existing_comment="已核验 DOI 且已取得可处理正文的研究文献书目信息",
    )
    op.alter_column(
        "papers",
        "citation_verified_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        comment="标题、年份和引文完成核验的时间",
        existing_comment="DOI 题录核验完成的时间",
        existing_nullable=False,
        existing_server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    op.alter_column(
        "papers",
        "citation_text",
        existing_type=sa.TEXT(),
        comment="已核验的 GB/T 7714-2015 引文文本",
        existing_comment="默认展示的 GB/T 7714-2015 引文缓存",
        existing_nullable=False,
    )
    op.alter_column(
        "papers",
        "paper_type",
        existing_type=sa.VARCHAR(length=64),
        comment="文献类型，如 journal-article 或 preprint",
        existing_comment="规整文献类型，如 journal_article 或 preprint",
        existing_nullable=True,
    )
    op.alter_column(
        "papers",
        "authors",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment="有序作者数组，来自结构化书目元数据",
        existing_comment="有序作者数组；每项为 literal 或 given 与 family 的结构化姓名",
        existing_nullable=False,
    )
    op.drop_constraint(op.f("ck_papers_paper_type"), "papers", type_="check")
    op.drop_constraint(op.f("ck_papers_publication_day_requires_month"), "papers", type_="check")
    op.drop_constraint(op.f("ck_papers_publication_day_range"), "papers", type_="check")
    op.drop_constraint(op.f("ck_papers_publication_month_range"), "papers", type_="check")
    op.drop_column("papers", "publisher")
    op.drop_column("papers", "article_number")
    op.drop_column("papers", "pages")
    op.drop_column("papers", "issue")
    op.drop_column("papers", "volume")
    op.drop_column("papers", "publication_day")
    op.drop_column("papers", "publication_month")

    op.drop_index("uq_papers_doi", table_name="papers")
    op.create_index(
        "uq_papers_doi",
        "papers",
        ["doi"],
        unique=True,
        postgresql_where=sa.text("doi IS NOT NULL"),
    )
    op.alter_column(
        "papers",
        "doi",
        existing_type=sa.VARCHAR(length=512),
        nullable=True,
        comment="规范化 DOI，可为空",
        existing_comment="规范化且唯一的 DOI",
    )
    op.add_column(
        "papers",
        sa.Column(
            "bibliographic_fingerprint",
            sa.String(length=64),
            nullable=True,
            comment="无 DOI 文献的规范书目去重指纹",
        ),
    )
    # DOI 在本次升级后唯一，md5(doi) 可安全恢复旧结构所需的非空唯一占位键。
    op.execute("UPDATE papers SET bibliographic_fingerprint = md5(doi)")
    op.alter_column(
        "papers",
        "bibliographic_fingerprint",
        existing_type=sa.VARCHAR(length=64),
        nullable=False,
        existing_comment="无 DOI 文献的规范书目去重指纹",
    )
    op.create_unique_constraint(
        op.f("uq_papers_bibliographic_fingerprint"), "papers", ["bibliographic_fingerprint"]
    )
