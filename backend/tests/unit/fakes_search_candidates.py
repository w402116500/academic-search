"""单元测试用的内存候选仓储。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.modules.documents.contracts import CandidateFulltextState
from app.modules.search.contracts import UnifiedCandidate


class FakeSearchCandidateRepository:
    """模拟 SearchCandidateRepository 的持久事实语义。"""

    def __init__(
        self,
        *,
        search_run_id: UUID,
        candidates: Sequence[UnifiedCandidate] = (),
        fulltext_states: Sequence[CandidateFulltextState] = (),
    ) -> None:
        self._candidates_by_run: dict[UUID, dict[UUID, UnifiedCandidate]] = {}
        self._order_by_run: dict[UUID, list[UUID]] = {}
        self._selected_by_run: dict[UUID, set[UUID]] = {}
        self._retry_by_run: dict[UUID, dict[UUID, int]] = {}
        self._fulltext_states: dict[tuple[UUID, UUID], CandidateFulltextState] = {}
        self.upsert_calls: list[tuple[UUID, tuple[UUID, ...]]] = []
        self.readiness_updates: list[tuple[UUID, tuple[UUID, ...]]] = []

        self._candidates_by_run[search_run_id] = {
            candidate.candidate_id: candidate for candidate in candidates
        }
        self._order_by_run[search_run_id] = [candidate.candidate_id for candidate in candidates]
        self._selected_by_run[search_run_id] = set()
        self._retry_by_run[search_run_id] = {}
        for state in fulltext_states:
            self._fulltext_states[(state.search_run_id, state.candidate.candidate_id)] = state

    async def upsert_candidates(
        self,
        *,
        search_run_id: UUID,
        candidates: Sequence[UnifiedCandidate],
    ) -> None:
        self.upsert_calls.append(
            (search_run_id, tuple(candidate.candidate_id for candidate in candidates))
        )
        self._candidates_by_run[search_run_id] = {
            candidate.candidate_id: candidate for candidate in candidates
        }
        self._order_by_run[search_run_id] = [candidate.candidate_id for candidate in candidates]
        self._selected_by_run.setdefault(search_run_id, set())
        self._retry_by_run[search_run_id] = {}

    async def list_candidates(self, *, search_run_id: UUID) -> tuple[UnifiedCandidate, ...]:
        candidates = self._candidates_by_run.get(search_run_id, {})
        return tuple(
            candidates[candidate_id]
            for candidate_id in self._order_by_run.get(search_run_id, [])
            if candidate_id in candidates
        )

    async def get_candidate(
        self,
        *,
        search_run_id: UUID,
        candidate_id: UUID,
    ) -> UnifiedCandidate | None:
        return self._candidates_by_run.get(search_run_id, {}).get(candidate_id)

    async def selected_ids(self, *, search_run_id: UUID) -> set[UUID]:
        return set(self._selected_by_run.get(search_run_id, set()))

    async def set_selected(
        self,
        *,
        search_run_id: UUID,
        candidate_ids: Sequence[UUID],
        selected: bool,
    ) -> int:
        current = self._selected_by_run.setdefault(search_run_id, set())
        known_ids = set(self._candidates_by_run.get(search_run_id, {}))
        requested = set(candidate_ids) & known_ids
        if selected:
            current.update(requested)
        else:
            current.difference_update(requested)
        return len(current)

    async def clear_selection(self, *, search_run_id: UUID) -> None:
        self._selected_by_run.setdefault(search_run_id, set()).clear()

    async def prune_selection(
        self,
        *,
        search_run_id: UUID,
        allowed_candidate_ids: set[UUID],
    ) -> None:
        self._selected_by_run.setdefault(search_run_id, set()).intersection_update(
            allowed_candidate_ids
        )

    async def get_fulltext_state(
        self,
        *,
        search_run_id: UUID,
        candidate_id: UUID,
    ) -> CandidateFulltextState | None:
        return self._fulltext_states.get((search_run_id, candidate_id))

    async def list_fulltext_states(
        self,
        *,
        search_run_id: UUID,
        candidate_ids: Sequence[UUID],
    ) -> dict[UUID, CandidateFulltextState]:
        return {
            candidate_id: state
            for candidate_id in candidate_ids
            if (state := self._fulltext_states.get((search_run_id, candidate_id))) is not None
        }

    async def write_fulltext_state(self, state: CandidateFulltextState) -> None:
        self._fulltext_states[(state.search_run_id, state.candidate.candidate_id)] = state

    async def update_relevance(
        self,
        *,
        search_run_id: UUID,
        candidates: Sequence[UnifiedCandidate],
    ) -> None:
        stored = self._candidates_by_run.setdefault(search_run_id, {})
        for candidate in candidates:
            if candidate.candidate_id in stored:
                stored[candidate.candidate_id] = candidate

    async def update_relevance_and_schedule_retry(
        self,
        *,
        search_run_id: UUID,
        resolved_candidates: Sequence[UnifiedCandidate],
        retry_attempt_no: int,
        retry_candidate_ids: Sequence[UUID],
    ) -> None:
        await self.update_relevance(
            search_run_id=search_run_id,
            candidates=resolved_candidates,
        )
        retry = self._retry_by_run.setdefault(search_run_id, {})
        retry.clear()
        known_ids = set(self._candidates_by_run.get(search_run_id, {}))
        for candidate_id in retry_candidate_ids:
            if candidate_id in known_ids:
                retry[candidate_id] = retry_attempt_no

    async def current_relevance_attempt_no(self, *, search_run_id: UUID) -> int:
        attempts = self._retry_by_run.get(search_run_id, {}).values()
        return max(attempts, default=1)

    async def relevance_retry_candidate_ids(
        self,
        *,
        search_run_id: UUID,
        attempt_no: int,
    ) -> frozenset[UUID] | None:
        retry_ids = frozenset(
            candidate_id
            for candidate_id, stored_attempt_no in self._retry_by_run.get(search_run_id, {}).items()
            if stored_attempt_no == attempt_no
        )
        return retry_ids or None

    async def clear_relevance_retry(self, *, search_run_id: UUID) -> None:
        self._retry_by_run.setdefault(search_run_id, {}).clear()

    async def update_readiness(
        self,
        *,
        search_run_id: UUID,
        candidates: Sequence[UnifiedCandidate],
    ) -> None:
        self.readiness_updates.append(
            (search_run_id, tuple(candidate.candidate_id for candidate in candidates))
        )
        stored = self._candidates_by_run.setdefault(search_run_id, {})
        for candidate in candidates:
            if candidate.candidate_id in stored:
                stored[candidate.candidate_id] = candidate
