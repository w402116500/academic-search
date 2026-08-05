"""对话、研究运行与回答证据模型。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.infra.db.models.collection import ResearchCollection
    from app.infra.db.models.document import DocumentChunk
    from app.infra.db.models.user import User


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """一个研究工作区内的用户对话。

    对话的 ``collection_id`` 决定后续消息发起 RAG 时允许检索的文献范围。
    """

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived', 'deleted')", name="status"),
        {"comment": "研究工作区内的一条用户对话"},
    )

    collection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("research_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="对话所属研究工作区标识",
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="发起对话的用户标识",
    )
    title: Mapped[str | None] = mapped_column(
        String(300), nullable=True, comment="自动生成或用户编辑的对话标题"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", index=True, comment="对话状态"
    )

    collection: Mapped[ResearchCollection] = relationship(back_populates="conversations")
    owner: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )
    # 会话被物理删除时由 research_runs.conversation_id 的数据库级联清理运行；
    # 普通用户删除仍是软删除，不会触发这里的物理删除行为。
    research_runs: Mapped[list[ResearchRun]] = relationship(
        back_populates="conversation",
        passive_deletes=True,
    )


class Message(UUIDPrimaryKeyMixin, Base):
    """对话中一条用户、助手或系统消息。

    ``metadata_json`` 仅保存流式展示等扩展信息；回答引用的原文证据必须写入
    ``research_evidences``，不能只留在消息 JSON 中。
    """

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="role"),
        CheckConstraint(
            "status IN ('pending', 'streaming', 'completed', 'failed')",
            name="status",
        ),
        Index("ix_messages_conversation_created_at", "conversation_id", "created_at"),
        {"comment": "对话中的用户、助手或系统消息"},
    )

    conversation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="消息所属对话标识",
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True, comment="消息角色：user、assistant 或 system"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息正文")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True, comment="消息生成状态"
    )
    # DeclarativeBase 的 metadata 为保留属性，
    # 所以 Python 侧使用 metadata_json 映射数据库 metadata 列。
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="消息展示扩展信息，不保存证据真相",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="消息创建时间",
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ResearchRun(UUIDPrimaryKeyMixin, Base):
    """一次 RAG 或 LangGraph 研究执行。

    该表面向产品查询、失败恢复和审计；LangGraph checkpoint 仅保存图执行状态，
    不能替代这条业务运行记录。
    """

    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint(
            "stage IN "
            "('dispatch', 'preparing', 'hybrid_retrieval', 'parent_merging', "
            "'reranking', 'evidence_verifying', 'answering', 'completed', "
            "'awaiting_clarification', 'failed', 'cancelled')",
            name="stage",
        ),
        CheckConstraint(
            "mode IN ('single_rag', 'multi_agent', 'research_note')",
            name="mode",
        ),
        CheckConstraint(
            "status IN "
            "('queued', 'running', 'awaiting_clarification', 'completed', 'failed', 'cancelled')",
            name="status",
        ),
        Index("ix_research_runs_conversation_created_at", "conversation_id", "created_at"),
        {"comment": "一次 RAG 或 LangGraph 研究执行的状态与审计信息"},
    )

    conversation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="触发本次运行的研究会话标识",
    )
    collection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("research_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="本次运行限定的研究工作区标识",
    )
    input_message_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="RESTRICT"),
        nullable=False,
        # 一条用户消息只有一个权威执行记录；重试在同一 research_run 内完成。
        unique=True,
        comment="触发本次研究的用户消息标识",
    )
    arq_job_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, comment="Redis arq 研究任务标识"
    )
    output_message_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        comment="最终回答或澄清消息标识",
    )
    mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="single_rag",
        index=True,
        comment="执行模式：single_rag、multi_agent 或 research_note",
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", index=True, comment="研究运行状态"
    )
    stage: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="dispatch",
        index=True,
        comment="可展示执行阶段：dispatch、检索、证据核验、回答或终态",
    )
    langgraph_thread_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, comment="LangGraph checkpoint 线程标识"
    )
    model_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, comment="模型、提示词版本与参数快照"
    )
    retrieval_trace: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="查询改写、召回和重排过程的审计摘要",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="机器可识别的失败代码"
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="可展示的失败原因"
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="用户请求协作停止的时间"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="运行开始时间"
    )
    stage_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="当前公开阶段开始时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="运行结束时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="研究运行记录创建时间",
    )

    collection: Mapped[ResearchCollection] = relationship(back_populates="research_runs")
    conversation: Mapped[Conversation] = relationship(back_populates="research_runs")
    evidences: Mapped[list[ResearchEvidence]] = relationship(
        back_populates="research_run",
        cascade="all, delete-orphan",
    )


class ResearchEvidence(UUIDPrimaryKeyMixin, Base):
    """一次研究运行中参与召回或最终回答的原文证据。

    ``is_cited`` 为真时表示片段被最终回答采用；分数列保留不同召回阶段的结果，
    便于后续离线评估检索与重排效果。
    """

    __tablename__ = "research_evidences"
    __table_args__ = (
        CheckConstraint(
            "selection_stage IN ('vector', 'rrf', 'rerank', 'final_citation')",
            name="selection_stage",
        ),
        {"comment": "一次研究运行中参与召回或最终回答的原文证据"},
    )

    research_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属研究运行标识",
    )
    chunk_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="原始证据片段标识",
    )
    selection_stage: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        comment="证据出现阶段：vector、rrf、rerank 或 final_citation",
    )
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="该阶段的排序名次")
    vector_score: Mapped[float | None] = mapped_column(Float, nullable=True, comment="向量召回分数")
    rrf_score: Mapped[float | None] = mapped_column(Float, nullable=True, comment="RRF 融合分数")
    rerank_score: Mapped[float | None] = mapped_column(Float, nullable=True, comment="精排分数")
    is_cited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True, comment="是否进入最终回答的引用列表"
    )
    citation_excerpt: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="回答当时展示的原文片段快照"
    )
    locator_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="页码、章节等定位信息快照"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="证据记录创建时间",
    )

    research_run: Mapped[ResearchRun] = relationship(back_populates="evidences")
    chunk: Mapped[DocumentChunk] = relationship(back_populates="evidences")
