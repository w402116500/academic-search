"""异步 PostgreSQL 的    迁移入口。"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from app.core.env import load_env

# models.__init__ 会集中导入所有模型，使 target_metadata 包含完整表集合。
from app.db.models import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

load_env()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """获取用于迁移的 asyncpg 连接串。"""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required to run Alembic migrations")
    return database_url


def run_migrations_offline() -> None:
    """在不连接数据库的情况下生成 SQL。

    该模式用于审阅部署 SQL；日常本地开发使用在线模式直接更新 PostgreSQL。
    """
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在已打开的同步连接中执行迁移步骤。

    ``Connection`` 是 ``AsyncConnection.run_sync`` 传入的同步适配对象，
    因此可以复用 Alembic 标准 MigrationContext。
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """使用 SQLAlchemy asyncpg 引擎连接 PostgreSQL。"""
    # Alembic 是短生命周期命令，不需要保留连接池中的空闲连接。
    connectable = create_async_engine(_database_url(), poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        # Alembic 内部仍使用同步 MigrationContext，通过 run_sync 安全桥接 asyncpg 连接。
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """以异步数据库连接执行迁移。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
