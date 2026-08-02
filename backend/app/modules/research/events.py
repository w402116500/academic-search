"""研究运行的 Redis Stream 事件存储。"""

from __future__ import annotations

import json
from uuid import UUID

from app.modules.research.contracts import ResearchProgressEvent
from redis.asyncio import Redis


class ResearchEventStore:
    """保存短期进度事件；PostgreSQL 仍是刷新和审计时的权威状态来源。"""

    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def publish(self, event: ResearchProgressEvent) -> str:
        """写入一条可重放的公开进度事件，并续期当前运行的事件流。"""
        key = self.stream_key(event.run_id)
        event_id = await self._redis.xadd(
            key,
            {"payload": json.dumps(event.model_dump(mode="json"), ensure_ascii=False)},
        )
        await self._redis.expire(key, self._ttl_seconds)
        return str(event_id)

    async def read_events(
        self,
        research_run_id: UUID,
        *,
        last_event_id: str,
        block_milliseconds: int = 5_000,
    ) -> tuple[tuple[str, dict[str, object]], ...]:
        """从上次事件 ID 后读取事件；断线客户端可使用 Last-Event-ID 恢复。"""
        response = await self._redis.xread(
            {self.stream_key(research_run_id): last_event_id},
            count=50,
            block=block_milliseconds,
        )
        events: list[tuple[str, dict[str, object]]] = []
        for _stream, items in response:
            for event_id, fields in items:
                raw_payload = fields.get("payload")
                if raw_payload is None:
                    continue
                try:
                    payload = json.loads(raw_payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    events.append((str(event_id), payload))
        return tuple(events)

    @staticmethod
    def stream_key(research_run_id: UUID) -> str:
        """将运行 ID 映射为与检索会话隔离的 Redis Stream 键。"""
        return f"research:run:{research_run_id}:events"
