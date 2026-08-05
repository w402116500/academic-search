"""Redis 检索会话快照和事件游标测试。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.infra.redis.search_session import RedisSearchSessionStore


class FakeRedis:
    """覆盖会话存储所需 Redis 方法的内存替身，不依赖本地 Redis 服务。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._next_event_number = 1

    async def set(self, key: str, value: str, *, ex: int) -> bool:
        self.values[key] = value
        self.expirations[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        """按传入顺序返回多个值，模拟 redis-py 的批量读取行为。"""
        return [self.values.get(key) for key in keys]

    async def xadd(
        self,
        key: str,
        fields: dict[str, str],
        *,
        maxlen: int,
        approximate: bool,
    ) -> str:
        _ = maxlen, approximate
        event_id = f"{self._next_event_number}-0"
        self._next_event_number += 1
        self.streams.setdefault(key, []).append((event_id, fields))
        return event_id

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True

    async def xread(
        self,
        streams: dict[str, str],
        *,
        count: int,
        block: int,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        _ = block
        result: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for key, last_event_id in streams.items():
            records = self.streams.get(key, [])
            if last_event_id == "$":
                selected: list[tuple[str, dict[str, str]]] = []
            else:
                last_number = int(last_event_id.split("-", maxsplit=1)[0])
                selected = [
                    record
                    for record in records
                    if int(record[0].split("-", maxsplit=1)[0]) > last_number
                ][:count]
            if selected:
                result.append((key, selected))
        return result


@pytest.mark.asyncio
async def test_snapshot_round_trip_refreshes_ttl() -> None:
    """候选快照以 JSON 保存，并在每次覆盖时刷新会话 TTL。"""
    redis = FakeRedis()
    store = RedisSearchSessionStore(redis, ttl_seconds=7200)  # type: ignore[arg-type]
    snapshot: dict[str, Any] = {"status": "running", "candidates": [{"title": "绿地"}]}

    await store.write_snapshot("search:1", snapshot)

    assert json.loads(redis.values["search:1"]) == snapshot
    assert await store.read_snapshot("search:1") == snapshot
    assert redis.expirations["search:1"] == 7200


@pytest.mark.asyncio
async def test_events_are_read_after_cursor_and_refresh_stream_ttl() -> None:
    """事件读取只返回游标之后的记录，适配 SSE 断线重连。"""
    redis = FakeRedis()
    store = RedisSearchSessionStore(redis, ttl_seconds=300)  # type: ignore[arg-type]
    first_id = await store.append_event("search:1", {"stage": "provider_search"})
    second_id = await store.append_event("search:1", {"stage": "completed"})

    events = await store.read_events("search:1", last_event_id=first_id, block_ms=0)

    assert events == [(second_id, {"stage": "completed"})]
    assert redis.expirations["search:1:events"] == 300


@pytest.mark.asyncio
async def test_events_reject_invalid_cursor() -> None:
    """不接受任意字符串作为 Redis Stream 游标，避免注入错误读取语义。"""
    store = RedisSearchSessionStore(FakeRedis(), ttl_seconds=300)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="无效的 Redis Stream 事件 ID"):
        await store.read_events("search:1", last_event_id="latest")


@pytest.mark.asyncio
async def test_read_many_snapshots_preserves_requested_key_mapping() -> None:
    """候选分页批量读取全文状态时，缺失键不能错位到其他候选。"""
    redis = FakeRedis()
    store = RedisSearchSessionStore(redis, ttl_seconds=300)  # type: ignore[arg-type]
    await store.write_snapshot("search:1", {"candidate_id": "first"})
    await store.write_snapshot("search:3", {"candidate_id": "third"})

    snapshots = await store.read_many_snapshots(["search:1", "search:2", "search:3"])

    assert snapshots == {
        "search:1": {"candidate_id": "first"},
        "search:3": {"candidate_id": "third"},
    }
