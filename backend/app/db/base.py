"""SQLAlchemy 声明基类和所有模型共享的列定义。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 为自动生成的约束和索引提供稳定名称，避免每次 Alembic 比对都出现无意义差异。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """全部 PostgreSQL 模型的声明基类。

    模型模块集中导入后，Alembic 通过 ``Base.metadata`` 读取全部表、列、
    约束和数据库备注，并据此生成迁移。
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """为实体表提供应用层生成的 UUID 主键。

    不使用数据库自增 ID，因此在对象尚未写入数据库时即可取得稳定标识。
    ``collection_papers`` 使用复合主键，不继承此混入类。
    """

    # 在 flush 前即拥有主键，便于一次事务内建立跨表关系和生成对象存储键。
    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="主键标识，由应用层生成 UUID",
    )


class TimestampMixin:
    """为可修改实体提供统一的 UTC 审计时间。

    ``updated_at`` 由 SQLAlchemy ORM 更新；直接执行 SQL 更新时不会自动变更，
    如有该需求应在 PostgreSQL 中额外建立触发器。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="创建时间，统一保存为 UTC",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="最近一次通过 ORM 更新的时间，统一保存为 UTC",
    )
