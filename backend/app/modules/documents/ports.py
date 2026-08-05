"""Ports consumed by the candidate full-text use case."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from app.modules.documents.contracts import FulltextCandidate


class CandidateFulltextRun(Protocol):
    """Minimum durable search-run projection needed by full-text preparation."""

    @property
    def id(self) -> UUID: ...

    @property
    def status(self) -> str: ...

    @property
    def redis_session_key(self) -> str | None: ...


class CandidateFulltextRunPort(Protocol):
    """Resolve an owned search run without exposing its persistence model."""

    async def get_owned_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> CandidateFulltextRun | None: ...


class CandidateFulltextLookupPort(Protocol):
    """Resolve one eligible server-side candidate as a Documents projection."""

    async def get(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        candidate_id: UUID,
    ) -> FulltextCandidate: ...


class CandidateFulltextSessionStore(Protocol):
    """Persist full-text state and its upload lease in the shared Redis session."""

    async def read_snapshot(self, session_key: str) -> dict[str, Any] | None: ...

    async def write_snapshot(self, session_key: str, snapshot: dict[str, Any]) -> None: ...

    async def try_acquire_lock(
        self,
        key: str,
        *,
        token: str,
        ttl_seconds: int,
    ) -> bool: ...

    async def release_lock(self, key: str, *, token: str) -> None: ...
