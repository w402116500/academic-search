"""研究工作区及工作区文献关联模型。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.document import Document
    from app.db.models.paper import Paper
    from app.db.models.research import Conversation, ResearchRun
    from app.db.models.user import User


class ResearchCollection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """用户的研究工作区，也是 RAG 的权限边界。

    文献文件、对话和研究运行都以此表的 ``id`` 限定范围。Milvus 的过滤字段
    可以使用该标识加速检索，但权限真相仍以 PostgreSQL 为准。
    """

    __tablename__ = "research_collections"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived', 'deleted')", name="status"),
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

    owner: Mapped[User] = relationship(back_populates="research_collections")
    collection_papers: Mapped[list[CollectionPaper]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
    )
    research_runs: Mapped[list[ResearchRun]] = relationship(back_populates="collection")


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
    documents: Mapped[list[Document]] = relationship(
        back_populates="collection_paper",
        cascade="all, delete-orphan",
    )
