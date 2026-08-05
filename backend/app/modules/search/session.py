"""Search-owned Redis key contract and short-lived session port."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID

SEARCH_SESSION_KEY_PREFIX = "academic-search:search-run"


def build_search_session_key(search_run_id: UUID) -> str:
    """Return the stable session key for one search run."""
    return f"{SEARCH_SESSION_KEY_PREFIX}:{search_run_id}"


def build_search_event_stream_key(search_run_id: UUID) -> str:
    """Return the stable event-stream key for one search run."""
    return f"{build_search_session_key(search_run_id)}:events"


def _validated_session_key(session_key: str, purpose: str) -> str:
    if not session_key.startswith(f"{SEARCH_SESSION_KEY_PREFIX}:"):
        raise ValueError(f"{purpose}必须位于服务端生成的检索会话键下")
    return session_key


def build_candidate_selection_key(session_key: str) -> str:
    """Return the stable short-lived selection key for a search session."""
    return f"{_validated_session_key(session_key, '候选准备清单')}:candidate-selection"


def build_candidate_selection_lock_key(session_key: str) -> str:
    """Return the stable selection-update lock key for a search session."""
    return f"{_validated_session_key(session_key, '候选准备清单锁')}:candidate-selection-lock"


def build_candidate_relevance_lock_key(session_key: str) -> str:
    """Return the stable renewable lease key for relevance execution."""
    return f"{_validated_session_key(session_key, '相关性运行锁')}:relevance-lock"


class SearchSessionStore(Protocol):
    """Persistence port for short-lived candidate snapshots, events, and leases."""

    async def write_snapshot(self, session_key: str, snapshot: dict[str, Any]) -> None: ...

    async def merge_snapshot(
        self,
        session_key: str,
        transform: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        max_attempts: int = 8,
    ) -> dict[str, Any]: ...

    async def read_snapshot(self, session_key: str) -> dict[str, Any] | None: ...

    async def read_many_snapshots(self, session_keys: list[str]) -> dict[str, dict[str, Any]]: ...

    async def refresh_ttl(self, session_key: str) -> None: ...

    async def append_event(self, session_key: str, event: dict[str, Any]) -> str: ...

    async def try_acquire_lock(self, key: str, *, token: str, ttl_seconds: int) -> bool: ...

    async def renew_lock(self, key: str, *, token: str, ttl_seconds: int) -> bool: ...

    async def renew_arq_in_progress(self, job_id: str, *, ttl_seconds: int) -> None: ...

    async def release_lock(self, key: str, *, token: str) -> None: ...

    async def read_events(
        self,
        session_key: str,
        *,
        last_event_id: str,
        block_ms: int = 15_000,
        count: int = 20,
    ) -> list[tuple[str, dict[str, Any]]]: ...
