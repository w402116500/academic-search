"""异步 PostgreSQL 引擎、会话工厂和 FastAPI 依赖。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 模块导入时先加载根目录 .env，确保命令行、测试和 Web 进程使用相同连接配置。
from app.core.env import load_env

load_env()


def _database_url() -> str:
    """读取 SQLAlchemy asyncpg 连接串，并在缺失时尽早失败。

    连接串只从环境变量读取，模型代码不保存任何本地开发密码或生产凭据。
    """
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required to create a database session")
    return database_url


engine = create_async_engine(
    _database_url(),
    # Docker Desktop 重启 PostgreSQL 后，借出连接前先探测，避免复用失效连接。
    pool_pre_ping=True,
)
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    # 路由提交后仍可读取对象字段，避免异步上下文中发生隐式懒加载。
    expire_on_commit=False,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """向 API 路由提供一次请求范围内的异步数据库会话。

    此依赖只负责创建和关闭会话，不替业务服务自动提交或回滚事务。
    """
    async with async_session_factory() as session:
        yield session


async def dispose_database_engine() -> None:
    """在应用关闭或测试结束时释放连接池。"""
    await engine.dispose()
