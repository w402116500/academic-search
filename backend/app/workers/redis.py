"""arq Worker 与 API 共享的 Redis 连接配置。"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from arq.connections import RedisSettings


def redis_settings_from_environment() -> RedisSettings:
    """从唯一的 ``REDIS_URL`` 解析 arq 配置，避免 Worker 和 API 配置漂移。"""
    raw_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"redis", "rediss"} or parsed.hostname is None:
        raise RuntimeError("REDIS_URL 必须是完整的 redis:// 或 rediss:// 地址")

    database_text = parsed.path.strip("/") or "0"
    if not database_text.isdecimal():
        raise RuntimeError("REDIS_URL 的数据库编号必须是非负整数")

    # Docker Desktop 的 Redis 默认仅发布 IPv4；Windows 的 localhost 会优先解析到 ::1。
    host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
    return RedisSettings(
        host=host,
        port=parsed.port or 6379,
        database=int(database_text),
        username=parsed.username,
        password=parsed.password,
        ssl=parsed.scheme == "rediss",
    )
