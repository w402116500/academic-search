"""用户领域的 PostgreSQL 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.collection import ResearchCollection
    from app.db.models.research import Conversation


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """本地账号及其拥有的研究工作区。

    密码只保存 Argon2id 哈希。邮箱可以为空，以兼容未来的匿名搜索或 OAuth
    绑定流程；实际登录策略由认证服务实现。
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="status"),
        # 邮箱允许为空以兼容匿名 / OAuth；存在邮箱时才按大小写无关规则唯一。
        Index(
            "uq_users_email_lower",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        {"comment": "用户账号与登录状态"},
    )

    email: Mapped[str | None] = mapped_column(
        String(320), nullable=True, comment="登录邮箱，按大小写无关规则唯一"
    )
    password_hash: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Argon2id 密码哈希，绝不保存明文"
    )
    password_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="最近一次修改密码的时间",
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="邮箱完成验证的时间",
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="用户展示名称")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", comment="账号状态：active 或 disabled"
    )

    research_collections: Mapped[list[ResearchCollection]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
    )
    # 对话通过 owner_user_id 的 ON DELETE CASCADE 删除；不让 ORM 先写入 NULL，
    # 以保持 conversations.owner_user_id 的非空约束和数据库级联语义一致。
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="owner",
        passive_deletes=True,
    )
