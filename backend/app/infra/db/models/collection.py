"""研究工作区及工作区文献关联模型。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.research.state import WorkspaceWorkflowStage

if TYPE_CHECKING:
    from app.infra.db.models.document import Document
    from app.infra.db.models.paper import Paper
    from app.infra.db.models.research import Conversation, ResearchRun
    from app.infra.db.models.user import User
    from app.infra.db.models.workflow import ResearchPlan, SearchRun


class ResearchCollection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """用户的研究工作区，也是 RAG 的权限边界。

    文献文件、对话和研究运行都以此表的 ``id`` 限定范围。Milvus 的过滤字段
    可以使用该标识加速检索，但权限真相仍以 PostgreSQL 为准。
    """

    __tablename__ = "research_collections"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived', 'deleted', 'deleting')", name="status"),
        CheckConstraint(
            "workflow_stage IN ('draft', 'analyzing', 'plan_review', 'retrieving', "
            "'screening', 'collection_building', 'researching', 'failed')",
            name="workflow_stage",
        ),
        # 工作区列表按所有者、状态和最近更新读取，这个索引避免用户数据增长后全表排序。
        Index(
            "ix_research_collections_owner_status_updated_at",
            "owner_user_id",
            "status",
            "updated_at",
        ),
        {"comment": "用户的研究工作区，也是 RAG 检索权限边界"},
    )

    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="工作区所有者的用户标识",
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="工作区名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="工作区说明")
    research_question: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="当前长期研究问题或研究范围"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", index=True, comment="工作区状态"
    )
    workflow_stage: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=WorkspaceWorkflowStage.DRAFT.value,
        server_default=text("'draft'"),
        index=True,
        comment="研究流程阶段：draft 草稿、analyzing 意图解析中、plan_review 计划待确认、"
        "retrieving 文献检索中、screening 候选筛选、collection_building 集合构建中、"
        "researching 可以证据研究、failed 执行失败",
    )

    owner: Mapped[User] = relationship(back_populates="research_collections")
    collection_papers: Mapped[list[CollectionPaper]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
    )
    bibliography_entries: Mapped[list[CollectionBibliographyEntry]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
    )
    # ResearchRun 的外键已经声明 ON DELETE CASCADE；交给数据库级联，避免 ORM
    # 在删除工作区时先将 collection_id 置空而违反 research_runs 的非空约束。
    research_runs: Mapped[list[ResearchRun]] = relationship(
        back_populates="collection",
        passive_deletes=True,
    )
    research_plans: Mapped[list[ResearchPlan]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
    )
    search_runs: Mapped[list[SearchRun]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
    )


class CollectionBibliographyEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """用户在研究集合中保留的一条候选书目快照。"""

    __tablename__ = "collection_bibliography_entries"
    __table_args__ = (
        UniqueConstraint("collection_id", "id", name="collection_bibliography_entry_id"),
        UniqueConstraint(
            "collection_id",
            "source_search_run_id",
            "source_candidate_id",
            name="collection_source_candidate",
        ),
        CheckConstraint("status IN ('active', 'archived')", name="status"),
        CheckConstraint("jsonb_typeof(candidate_authors) = 'array'", name="candidate_authors"),
        CheckConstraint(
            "citation_status IN ('pending', 'ready', 'unavailable')",
            name="citation_status",
        ),
        CheckConstraint(
            "citation_text IS NULL OR citation_status = 'ready'",
            name="citation_text_requires_ready",
        ),
        CheckConstraint(
            "pdf_status IN ('unknown', 'available', 'requires_upload')",
            name="pdf_status",
        ),
        CheckConstraint(
            "content_status IN ("
            "'pending_auto_download', 'requires_upload', 'document_ready', "
            "'ingesting', 'researchable', 'failed', 'cancelled'"
            ")",
            name="content_status",
        ),
        CheckConstraint(
            "automatic_download_attempts BETWEEN 0 AND 2",
            name="automatic_download_attempts_range",
        ),
        Index(
            "ix_collection_bibliography_entries_collection_status_added_at",
            "collection_id",
            "status",
            "added_at",
        ),
        {"comment": "研究集合中用户保留的候选书目快照"},
    )

    collection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("research_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属研究工作区标识",
    )
    source_search_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("search_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="来源检索运行标识；历史或手动条目可以为空",
    )
    source_candidate_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="来源检索候选标识；不作为全局论文事实",
    )
    paper_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="已核验共享论文标识；题录不可用时为空",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        server_default=text("'active'"),
        index=True,
        comment="集合内书目状态：active 或 archived",
    )
    candidate_title: Mapped[str] = mapped_column(Text, nullable=False, comment="候选标题快照")
    candidate_authors: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="候选作者展示快照",
    )
    candidate_abstract: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="候选摘要快照"
    )
    candidate_publication_year: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True, index=True, comment="候选发表年份快照"
    )
    candidate_venue: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="候选来源期刊、会议或平台快照"
    )
    candidate_doi: Mapped[str | None] = mapped_column(
        String(512), nullable=True, index=True, comment="候选 DOI 展示快照"
    )
    candidate_source_url: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="候选来源页面或公开地址快照"
    )
    source_record: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="来源原始记录中允许持久保存的结构化快照",
    )
    citation_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
        index=True,
        comment="题录核验状态：pending、ready 或 unavailable",
    )
    citation_text: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="已核验时生成的正式引用文本"
    )
    citation_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="已核验题录或稳定失败状态的结构化快照",
    )
    pdf_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        server_default=text("'unknown'"),
        index=True,
        comment="公开 PDF 探测状态：unknown、available 或 requires_upload",
    )
    pdf_source_url: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="已探测可自动获取 PDF 的安全来源地址"
    )
    pdf_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="PDF 可得性探测的结构化快照",
    )
    content_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="requires_upload",
        server_default=text("'requires_upload'"),
        index=True,
        comment="内容处理状态，用于区分需上传、自动获取、入库中和已可研究",
    )
    automatic_download_attempts: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="系统自动下载 PDF 的已尝试次数，最多两次",
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
        comment="用户在当前工作区添加的标签列表",
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="用户对该书目的笔记")
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="加入当前工作区的时间",
    )

    collection: Mapped[ResearchCollection] = relationship(back_populates="bibliography_entries")
    paper: Mapped[Paper | None] = relationship(back_populates="bibliography_entries")
    documents: Mapped[list[Document]] = relationship(
        back_populates="bibliography_entry",
        cascade="all, delete-orphan",
    )


class CollectionPaper(Base):
    """已验证论文在单个研究工作区中的笔记和状态。

    复合主键确保同一论文在同一工作区只出现一次。未完成引文核验的搜索候选
    不会创建此记录。
    """

    __tablename__ = "collection_papers"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="status"),
        {"comment": "已验证论文在单个研究工作区中的关联、标签与笔记"},
    )

    collection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("research_collections.id", ondelete="CASCADE"),
        primary_key=True,
        comment="所属研究工作区标识",
    )
    paper_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="已验证论文标识",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        index=True,
        comment="工作区内状态：active 或 archived",
    )
    # 同时设置 ORM 和 PostgreSQL 默认值，避免通过脚本直接插入时出现 NULL 标签。
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
        comment="用户在当前工作区添加的标签列表",
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="用户对该论文的笔记")
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="加入当前工作区的时间",
    )

    collection: Mapped[ResearchCollection] = relationship(back_populates="collection_papers")
    paper: Mapped[Paper] = relationship(back_populates="collection_papers")
