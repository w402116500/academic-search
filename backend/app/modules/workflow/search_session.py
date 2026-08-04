"""检索候选在 Redis 中的短期会话键约定。

PostgreSQL 的 ``search_runs`` 只保存可恢复的运行状态、计数、错误与该会话键。
标题、摘要、来源原始字段和候选详情属于可再生的短期数据，必须在 Redis TTL
到期后失效，而不能绕过 DOI 和全文准入规则写入长期 ``papers`` 表。
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import WatchError

SEARCH_SESSION_KEY_PREFIX = "academic-search:search-run"
_STREAM_ID_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)-(?:0|[1-9][0-9]*)$|^\$$")


def build_search_session_key(search_run_id: UUID) -> str:
    """为一次检索运行生成唯一 Redis 键，不包含用户可控文本。"""
    return f"{SEARCH_SESSION_KEY_PREFIX}:{search_run_id}"


def build_search_event_stream_key(search_run_id: UUID) -> str:
    """为一次检索运行生成独立的 Redis Stream 键。"""
    return f"{build_search_session_key(search_run_id)}:events"


def build_candidate_fulltext_key(session_key: str, candidate_id: UUID) -> str:
    """为某次检索中的一个候选生成独立全文状态键。"""
    if not session_key.startswith(f"{SEARCH_SESSION_KEY_PREFIX}:"):
        raise ValueError("全文状态必须位于服务端生成的检索会话键下")
    return f"{session_key}:candidate:{candidate_id}:fulltext"


def build_candidate_fulltext_upload_lock_key(session_key: str, candidate_id: UUID) -> str:
    """为同一候选上传建立互斥锁，避免两个请求覆盖彼此的暂存结果。"""
    if not session_key.startswith(f"{SEARCH_SESSION_KEY_PREFIX}:"):
        raise ValueError("候选上传锁必须位于服务端生成的检索会话键下")
    return f"{session_key}:candidate:{candidate_id}:fulltext-upload-lock"


def build_candidate_selection_key(session_key: str) -> str:
    """为当前检索会话生成短期准备清单键，不与候选主快照混写。"""
    if not session_key.startswith(f"{SEARCH_SESSION_KEY_PREFIX}:"):
        raise ValueError("候选准备清单必须位于服务端生成的检索会话键下")
    return f"{session_key}:candidate-selection"


def build_candidate_selection_lock_key(session_key: str) -> str:
    """为准备清单更新生成互斥锁，避免多标签页覆盖彼此的勾选结果。"""
    if not session_key.startswith(f"{SEARCH_SESSION_KEY_PREFIX}:"):
        raise ValueError("候选准备清单锁必须位于服务端生成的检索会话键下")
    return f"{session_key}:candidate-selection-lock"


def build_candidate_relevance_lock_key(session_key: str) -> str:
    """为整个候选集合相关性运行建立可续约租约。"""
    if not session_key.startswith(f"{SEARCH_SESSION_KEY_PREFIX}:"):
        raise ValueError("相关性运行锁必须位于服务端生成的检索会话键下")
    return f"{session_key}:relevance-lock"


def build_candidate_relevance_cancel_key(session_key: str) -> str:
    """为当前相关性运行保存显式取消标记，不把控制状态交给浏览器连接。"""
    if not session_key.startswith(f"{SEARCH_SESSION_KEY_PREFIX}:"):
        raise ValueError("相关性取消标记必须位于服务端生成的检索会话键下")
    return f"{session_key}:relevance-cancel"


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

    async def merge_snapshot(
        self,
        session_key: str,
        transform: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        max_attempts: int = 8,
    ) -> dict[str, Any]:
        """以 WATCH/MULTI 合并最新快照，避免异步任务覆盖候选的其他字段更新。"""
        for _attempt in range(max_attempts):
            async with self._redis.pipeline(transaction=True) as pipe:
                await pipe.watch(session_key)
                raw_value = await pipe.get(session_key)
                if raw_value is None:
                    raise KeyError("检索候选会话已过期")
                if not isinstance(raw_value, str):
                    raise TypeError("Redis 检索会话快照必须是字符串")
                snapshot = json.loads(raw_value)
                if not isinstance(snapshot, dict):
                    raise ValueError("Redis 检索会话快照必须是 JSON 对象")
                merged = transform(snapshot)
                pipe.multi()
                pipe.set(
                    session_key,
                    json.dumps(merged, ensure_ascii=False),
                    ex=self._ttl_seconds,
                )
                try:
                    await pipe.execute()
                except WatchError:
                    continue
                return merged
        raise RuntimeError("检索候选快照在合并期间持续变化，请稍后重试")

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

    async def read_many_snapshots(self, session_keys: list[str]) -> dict[str, dict[str, Any]]:
        """一次读取多个同会话短期状态，供候选分页避免逐条 Redis 往返。"""
        if not session_keys:
            return {}

        raw_values = await self._redis.mget(session_keys)
        snapshots: dict[str, dict[str, Any]] = {}
        for key, raw_value in zip(session_keys, raw_values, strict=True):
            if raw_value is None:
                continue
            if not isinstance(raw_value, str):
                raise TypeError("Redis 检索会话快照必须是字符串")
            value = json.loads(raw_value)
            if not isinstance(value, dict):
                raise ValueError("Redis 检索会话快照必须是 JSON 对象")
            snapshots[key] = value
        return snapshots

    async def refresh_ttl(self, session_key: str) -> None:
        """刷新既有会话键的 TTL，不写回主快照以免覆盖并发的候选更新。"""
        await self._redis.expire(session_key, self._ttl_seconds)

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

    async def try_acquire_lock(self, key: str, *, token: str, ttl_seconds: int) -> bool:
        """尝试获取带租约的短期锁；同一候选的并发重试只允许一个请求执行。"""
        acquired = await self._redis.set(key, token, nx=True, ex=ttl_seconds)
        return bool(acquired)

    async def renew_lock(self, key: str, *, token: str, ttl_seconds: int) -> bool:
        """仅持有者可续期租约；过期或易主时不续到其他任务名下。"""
        result = self._redis.eval(
            """
            if redis.call('GET', KEYS[1]) == ARGV[1] then
                return redis.call('EXPIRE', KEYS[1], ARGV[2])
            end
            return 0
            """,
            1,
            key,
            token,
            str(ttl_seconds),
        )
        return bool(await cast(Awaitable[Any], result))

    async def request_relevance_cancellation(self, session_key: str) -> None:
        """写入与候选会话同 TTL 的取消标记，供长流 Worker 主动停止。"""
        await self._redis.set(
            build_candidate_relevance_cancel_key(session_key),
            "1",
            ex=self._ttl_seconds,
        )

    async def is_relevance_cancellation_requested(self, session_key: str) -> bool:
        """检查运行级取消标记；不记录模型正文或局部结论。"""
        return bool(await self._redis.exists(build_candidate_relevance_cancel_key(session_key)))

    async def clear_relevance_cancellation(self, session_key: str) -> None:
        """新一轮整批分析开始前清除旧的取消标记。"""
        await self._redis.delete(build_candidate_relevance_cancel_key(session_key))

    async def renew_arq_in_progress(self, job_id: str, *, ttl_seconds: int) -> None:
        """续期 ARQ 的占用标记，避免无总时长任务在运行中被重复领取。"""
        # arq 0.28 固定使用该前缀；这里只续期当前 Worker 传入的服务端 job_id。
        await self._redis.pexpire(f"arq:in-progress:{job_id}", ttl_seconds * 1_000)

    async def release_lock(self, key: str, *, token: str) -> None:
        """仅由持有者释放锁，避免过期后删除其他请求重新获得的锁。"""
        result = self._redis.eval(
            """
            if redis.call('GET', KEYS[1]) == ARGV[1] then
                return redis.call('DEL', KEYS[1])
            end
            return 0
            """,
            1,
            key,
            token,
        )
        # redis-py 的类型存根把 eval 标为同步返回，但 asyncio 客户端实际返回 awaitable。
        await cast(Awaitable[Any], result)

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
