"""候选审核准备清单的选择用例。"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.modules.search.api_contracts import CandidateSelectionResponse
from app.modules.search.review_session import (
    CandidateReviewError,
    CandidateReviewErrorCode,
    CandidateReviewSession,
)
from app.modules.search.session import build_candidate_selection_lock_key

_SELECTION_LOCK_TTL_SECONDS = 15


class CandidateSelectionService:
    """在 Redis 中原子维护一个搜索运行的准备清单。"""

    def __init__(self, session: CandidateReviewSession) -> None:
        self._session = session

    async def update_selection(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        candidate_ids: list[UUID],
        selected: bool,
    ) -> CandidateSelectionResponse:
        """原子增加或移除准备清单候选，避免多标签页相互覆盖。"""
        run = await self._session.owned_finished_run(owner_user_id, collection_id, search_run_id)
        _snapshot, all_candidates = await self._session.snapshot_candidates(run)
        candidates = self._session.visible_candidates(all_candidates)
        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        self._session.require_requested_candidates(
            candidate_ids, candidate_by_id, selected=selected
        )

        session_key = self._session.session_key(run)
        lock_key = build_candidate_selection_lock_key(session_key)
        lock_token = uuid4().hex
        acquired = await self._session.store.try_acquire_lock(
            lock_key,
            token=lock_token,
            ttl_seconds=_SELECTION_LOCK_TTL_SECONDS,
        )
        if not acquired:
            raise CandidateReviewError(
                CandidateReviewErrorCode.SELECTION_BUSY,
                "候选选择正在同步，请稍后重试。",
            )

        try:
            current_ids = await self._session.selected_ids(run)
            requested = set(candidate_ids)
            updated = current_ids | requested if selected else current_ids - requested
            await self._session.write_selected_ids(run, updated)
        finally:
            await self._session.store.release_lock(lock_key, token=lock_token)

        return CandidateSelectionResponse(run_id=run.id, selected_count=len(updated))

    async def clear_selection(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> CandidateSelectionResponse:
        """清空当前搜索会话的准备清单，不影响已持久化集合。"""
        run = await self._session.owned_finished_run(owner_user_id, collection_id, search_run_id)
        session_key = self._session.session_key(run)
        lock_key = build_candidate_selection_lock_key(session_key)
        lock_token = uuid4().hex
        acquired = await self._session.store.try_acquire_lock(
            lock_key,
            token=lock_token,
            ttl_seconds=_SELECTION_LOCK_TTL_SECONDS,
        )
        if not acquired:
            raise CandidateReviewError(
                CandidateReviewErrorCode.SELECTION_BUSY,
                "候选选择正在同步，请稍后重试。",
            )
        try:
            await self._session.write_selected_ids(run, set())
        finally:
            await self._session.store.release_lock(lock_key, token=lock_token)
        return CandidateSelectionResponse(run_id=run.id, selected_count=0)
