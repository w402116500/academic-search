"""Search-owned persistent candidate review repository contracts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.modules.documents.contracts import CandidateFulltextState
from app.modules.search.contracts import UnifiedCandidate


class SearchCandidateRepository(Protocol):
    """Durable storage boundary for candidate review facts."""

    async def upsert_candidates(
        self,
        *,
        search_run_id: UUID,
        candidates: Sequence[UnifiedCandidate],
    ) -> None: ...

    async def list_candidates(self, *, search_run_id: UUID) -> tuple[UnifiedCandidate, ...]: ...

    async def get_candidate(
        self,
        *,
        search_run_id: UUID,
        candidate_id: UUID,
    ) -> UnifiedCandidate | None: ...

    async def selected_ids(self, *, search_run_id: UUID) -> set[UUID]: ...

    async def set_selected(
        self,
        *,
        search_run_id: UUID,
        candidate_ids: Sequence[UUID],
        selected: bool,
    ) -> int: ...

    async def clear_selection(self, *, search_run_id: UUID) -> None: ...

    async def prune_selection(
        self,
        *,
        search_run_id: UUID,
        allowed_candidate_ids: set[UUID],
    ) -> None: ...

    async def get_fulltext_state(
        self,
        *,
        search_run_id: UUID,
        candidate_id: UUID,
    ) -> CandidateFulltextState | None: ...

    async def list_fulltext_states(
        self,
        *,
        search_run_id: UUID,
        candidate_ids: Sequence[UUID],
    ) -> dict[UUID, CandidateFulltextState]: ...

    async def write_fulltext_state(self, state: CandidateFulltextState) -> None: ...

    async def update_relevance(
        self,
        *,
        search_run_id: UUID,
        candidates: Sequence[UnifiedCandidate],
    ) -> None: ...

    async def update_relevance_and_schedule_retry(
        self,
        *,
        search_run_id: UUID,
        resolved_candidates: Sequence[UnifiedCandidate],
        retry_attempt_no: int,
        retry_candidate_ids: Sequence[UUID],
    ) -> None: ...

    async def current_relevance_attempt_no(self, *, search_run_id: UUID) -> int: ...

    async def relevance_retry_candidate_ids(
        self,
        *,
        search_run_id: UUID,
        attempt_no: int,
    ) -> frozenset[UUID] | None: ...

    async def clear_relevance_retry(self, *, search_run_id: UUID) -> None: ...

    async def update_readiness(
        self,
        *,
        search_run_id: UUID,
        candidates: Sequence[UnifiedCandidate],
    ) -> None: ...
