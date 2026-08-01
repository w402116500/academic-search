"""检索候选在 Redis 中的短期会话键约定。

PostgreSQL 的 ``search_runs`` 只保存可恢复的运行状态、计数、错误与该会话键。
标题、摘要、来源原始字段和候选详情属于可再生的短期数据，必须在 Redis TTL
到期后失效，而不能绕过 DOI 和全文准入规则写入长期 ``papers`` 表。
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

SEARCH_SESSION_KEY_PREFIX = "academic-search:search-run"
_STREAM_ID_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)-(?:0|[1-9][0-9]*)$|^\$$")


def build_search_session_key(search_run_id: UUID) -> str:
    """为一次检索运行生成唯一 Redis 键，不包含用户可控文本。"""
    return f"{SEARCH_SESSION_KEY_PREFIX}:{search_run_id}"


def build_search_event_stream_key(search_run_id: UUID) -> str:
    """为一次检索运行生成独立的 Redis Stream 键。"""
    return f"{build_search_session_key(search_run_id)}:events"


class SearchSessionStore:
    """保存检索运行的短期候选快照和可恢复进度事件。"""

    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        """接收调用方创建的 Redis 客户端，避免模块内部维护全局连接。"""
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def write_snapshot(self, session_key: str, snapshot: dict[str, Any]) -> None:
        """以 JSON 快照覆盖当前候选状态，并刷新 TTL。"""
        await self._redis.set(
            session_key,
            json.dumps(snapshot, ensure_ascii=False),
            ex=self._ttl_seconds,
        )

    async def read_snapshot(self, session_key: str) -> dict[str, Any] | None:
        """读取候选快照；Redis 中不存在时返回 None 供 API 区分过期状态。"""
        raw_value = await self._redis.get(session_key)
        if raw_value is None:
            return None
        if not isinstance(raw_value, str):
            raise TypeError("Redis 检索会话快照必须是字符串")
        value = json.loads(raw_value)
        if not isinstance(value, dict):
            raise ValueError("Redis 检索会话快照必须是 JSON 对象")
        return value

    async def append_event(self, session_key: str, event: dict[str, Any]) -> str:
        """写入一条进度事件，并让事件流与候选快照同步过期。"""
        stream_key = f"{session_key}:events"
        event_id = await self._redis.xadd(
            stream_key,
            {"payload": json.dumps(event, ensure_ascii=False)},
            maxlen=1000,
            approximate=True,
        )
        await self._redis.expire(stream_key, self._ttl_seconds)
        return str(event_id)

    async def read_events(
        self,
        session_key: str,
        *,
        last_event_id: str,
        block_ms: int = 15_000,
        count: int = 20,
    ) -> list[tuple[str, dict[str, Any]]]:
        """从指定事件 ID 之后读取事件，支持 SSE 断线恢复。"""
        if not _STREAM_ID_PATTERN.fullmatch(last_event_id):
            raise ValueError("无效的 Redis Stream 事件 ID")

        records = await self._redis.xread(
            {f"{session_key}:events": last_event_id},
            count=count,
            block=block_ms,
        )
        events: list[tuple[str, dict[str, Any]]] = []
        for _stream_name, stream_records in records:
            for event_id, fields in stream_records:
                payload = fields.get("payload")
                if not isinstance(payload, str):
                    raise ValueError("Redis 检索进度事件缺少 payload")
                event = json.loads(payload)
                if not isinstance(event, dict):
                    raise ValueError("Redis 检索进度事件必须是 JSON 对象")
                events.append((str(event_id), event))
        return events
