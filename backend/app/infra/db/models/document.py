"""文献文件、RAG 入库运行和分块模型。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.infra.db.models.collection import CollectionBibliographyEntry
    from app.infra.db.models.paper import Paper
    from app.infra.db.models.research import ResearchEvidence


class Document(UUIDPrimaryKeyMixin, Base):
    """研究集合书目条目取得的、可进入 RAG 入库链路的文件。

    ``collection_id`` 与 ``bibliography_entry_id`` 共同外键指向集合书目条目，
    从数据库层禁止将不属于当前工作区的候选文件写入 RAG 范围。
    """

    __tablename__ = "documents"
    __table_args__ = (
        # 文档必须归属“已加入当前工作区”的书目条目，防止跨工作区文件混入 RAG 索引。
        ForeignKeyConstraint(
            ["collection_id", "bibliography_entry_id"],
            ["collection_bibliography_entries.collection_id", "collection_bibliography_entries.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("collection_id", "sha256", name="collection_sha256"),
        CheckConstraint(
            "origin_kind IN ('user_upload', 'open_access', 'official_download')",
            name="origin_kind",
        ),
        CheckConstraint(
            "access_rights IN ('user_upload', 'open_access', 'official_allowed')",
            name="access_rights",
        ),
        {"comment": "研究工作区内可用于 RAG 的论文文件"},
    )

    collection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False, index=True, comment="所属研究工作区标识"
    )
    bibliography_entry_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False, index=True, comment="所属集合书目条目标识"
    )
    paper_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="对应已验证论文标识；题录不可用时为空",
    )
    origin_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="文件取得方式：user_upload、open_access 或 official_download",
    )
    original_filename: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="原始文件名"
    )
    media_type: Mapped[str] = mapped_column(String(128), nullable=False, comment="MIME 类型")
    byte_size: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="文件大小，单位为字节"
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, comment="文件内容 SHA-256 指纹")
    object_key: Mapped[str] = mapped_column(
        String(1024), nullable=False, unique=True, comment="MinIO 或 S3 中的私有对象键"
    )
    source_url: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="文件的合法取得来源地址"
    )
    access_rights: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="文件使用权限：user_upload、open_access 或 official_allowed",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="文件记录创建时间",
    )

    bibliography_entry: Mapped[CollectionBibliographyEntry] = relationship(
        back_populates="documents"
    )
    paper: Mapped[Paper | None] = relationship()
    ingestion_runs: Mapped[list[IngestionRun]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class IngestionRun(UUIDPrimaryKeyMixin, Base):
    """一次可重试的文件解析、切块与嵌入运行。

    Redis arq 只负责投递 ``arq_job_id`` 对应的任务；该表才是可恢复状态、
    配置快照和失败信息的持久化真相。
    """

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "pipeline_version", "attempt_no", name="document_pipeline_attempt"
        ),
        CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'completed', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint("stage IN ('parse', 'chunk', 'embed', 'index')", name="stage"),
        # 版本在全部处理完成前不能成为 RAG 检索入口。
        CheckConstraint(
            "NOT is_current OR status = 'completed'", name="current_requires_completed"
        ),
        Index(
            "uq_ingestion_runs_current_document",
            "document_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        {"comment": "一次可重试的文件解析、切块与向量写入运行"},
    )

    document_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="本次运行处理的文献文件标识",
    )
    arq_job_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, comment="Redis arq 异步任务标识"
    )
    pipeline_version: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="解析与向量化流程版本"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        index=True,
        comment="运行状态：pending 待确认、queued 已投递、running 执行中、"
        "completed 完成、failed 失败、cancelled 已取消",
    )
    stage: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="parse",
        index=True,
        comment="当前阶段：parse、chunk、embed 或 index",
    )
    parser_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="实际使用的解析器名称"
    )
    parser_version: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="实际使用的解析器版本"
    )
    chunking_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, comment="本次运行的分块配置快照"
    )
    embedding_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, comment="本次运行的嵌入模型配置快照"
    )
    # Worker 每个阶段逐步写入统计，不要求在投递任务时就准备完整 JSON。
    statistics: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="页数、片段数、耗时等运行统计",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="机器可识别的失败代码"
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="可展示的失败原因"
    )
    attempt_no: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, comment="同一流程版本的重试次数"
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="是否为当前可参与 RAG 检索的已完成版本",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="运行开始时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="运行结束时间"
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="工作区删除等系统操作请求协作停止的时间",
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="确认构建后实际投递到 Worker 的时间",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="运行记录创建时间",
    )

    document: Mapped[Document] = relationship(back_populates="ingestion_runs")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="ingestion_run",
        cascade="all, delete-orphan",
    )


class DocumentChunk(UUIDPrimaryKeyMixin, Base):
    """RAG 的可引用原文片段，L3 片段会写入 Milvus。

    Milvus 只保存向量和 ``id``，页码、章节、原文与父块关系始终从本表回查，
    因此回答可追溯到论文中的具体位置。
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("ingestion_run_id", "ordinal", name="ingestion_run_ordinal"),
        CheckConstraint("level BETWEEN 1 AND 3", name="level_range"),
        CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_start <= page_end",
            name="page_range",
        ),
        {"comment": "RAG 可引用原文片段；仅 L3 片段写入 Milvus"},
    )

    ingestion_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="生成该片段的入库运行标识",
    )
    # 删除父块时仅断开层级引用，避免历史证据因维护操作被级联删除。
    parent_chunk_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="直接父片段标识，用于分层上下文扩展",
    )
    root_chunk_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="顶层 L1 片段标识",
    )
    level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, index=True, comment="分块层级：1、2 或 3"
    )
    ordinal: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="当前入库版本内的稳定顺序"
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="原文片段内容，是引用展示的真相来源"
    )
    token_count: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="按嵌入模型统计的 token 数"
    )
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="片段起始页码")
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="片段结束页码")
    section_path: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, comment="章节层级路径"
    )
    locator: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="页内坐标、段落号和原文锚点等定位信息",
    )
    content_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="片段原文的 SHA-256 指纹"
    )

    ingestion_run: Mapped[IngestionRun] = relationship(back_populates="chunks")
    evidences: Mapped[list[ResearchEvidence]] = relationship(back_populates="chunk")
