"""已验证论文的书目信息模型。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, Index, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.infra.db.models.collection import CollectionPaper


# 文献来源规整层与引用格式化层共用这些下划线形式的内部值。数据库使用检查约束
# 拒绝旧的 ``journal-article`` 等外部格式，避免长期数据与格式化映射脱节。
_PAPER_TYPE_VALUES = (
    "journal_article",
    "conference_paper",
    "book",
    "book_chapter",
    "dissertation",
    "preprint",
    "posted_content",
    "dataset",
    "editorial",
    "correction",
    "grant",
    "peer_review",
    "reference_entry",
    "retraction",
    "other",
)
_PAPER_TYPE_SQL_VALUES = ", ".join(f"'{value}'" for value in _PAPER_TYPE_VALUES)


class Paper(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """已具备已核验 DOI 题录和可处理正文的研究文献。

    ``authors`` 保存格式中立题录的有序作者数据，``citation_text`` 仅缓存默认
    GB/T 7714-2015 展示文本。其他格式必须从本表的规范字段重新渲染，不能从
    格式化文本反向解析。
    """

    __tablename__ = "papers"
    __table_args__ = (
        CheckConstraint(
            "publication_year IS NULL OR publication_year BETWEEN 1000 AND 9999",
            name="publication_year_range",
        ),
        CheckConstraint(
            "publication_month IS NULL OR publication_month BETWEEN 1 AND 12",
            name="publication_month_range",
        ),
        CheckConstraint(
            "publication_day IS NULL OR publication_day BETWEEN 1 AND 31",
            name="publication_day_range",
        ),
        CheckConstraint(
            "publication_day IS NULL OR publication_month IS NOT NULL",
            name="publication_day_requires_month",
        ),
        # 作者以有序 JSON 数组保存，首版不再维护独立作者表。
        CheckConstraint("jsonb_typeof(authors) = 'array'", name="authors_array"),
        CheckConstraint(
            f"paper_type IS NULL OR paper_type IN ({_PAPER_TYPE_SQL_VALUES})",
            name="paper_type",
        ),
        # DOI 是长期文献与已获取正文之间的稳定关联键，不能允许空值绕过准入规则。
        Index("uq_papers_doi", "doi", unique=True),
        {"comment": "已核验 DOI 且已取得可处理正文的研究文献书目信息"},
    )

    doi: Mapped[str] = mapped_column(String(512), nullable=False, comment="规范化且唯一的 DOI")
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="论文标题")
    authors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        comment="有序作者数组；每项为 literal 或 given 与 family 的结构化姓名",
    )
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True, comment="论文摘要")
    publication_year: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True, index=True, comment="发表年份"
    )
    publication_month: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True, comment="发表月份，来自格式中立题录"
    )
    publication_day: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True, comment="发表日期，必须与月份同时存在"
    )
    venue: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="期刊、会议或预印本平台"
    )
    paper_type: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="规整文献类型，如 journal_article 或 preprint"
    )
    volume: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="卷号")
    issue: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="期号")
    pages: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="页码范围")
    article_number: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="文章号；与页码分别保留"
    )
    publisher: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="出版者")
    official_url: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="官方落地页或开放获取入口"
    )
    language: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="论文主语言，如 zh 或 en"
    )
    citation_text: Mapped[str] = mapped_column(
        Text, nullable=False, comment="默认展示的 GB/T 7714-2015 引文缓存"
    )
    citation_provider: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="引文或书目信息来源，如 Crossref"
    )
    citation_source_url: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="引文或书目信息的权威来源地址"
    )
    citation_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="DOI 题录核验完成的时间",
    )

    collection_papers: Mapped[list[CollectionPaper]] = relationship(back_populates="paper")
