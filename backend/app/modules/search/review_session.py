"""候选审核共享的运行归属、持久候选与短期锁边界。"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from app.modules.documents.contracts import CandidateFulltextState
from app.modules.search.candidate_repository import SearchCandidateRepository
from app.modules.search.contracts import UnifiedCandidate
from app.modules.search.relevance import is_screening_candidate
from app.modules.search.run_models import SearchRunRecord
from app.modules.search.run_repository import SearchRunRepository
from app.modules.search.run_service import SearchRunService
from app.modules.search.session import SearchSessionStore
from app.modules.search.state import SearchRunStatus


class CandidateReviewErrorCode(StrEnum):
    """候选审核接口向前端公开的稳定失败原因。"""

    SESSION_EXPIRED = "candidate_review_session_expired"
    SEARCH_NOT_FINISHED = "candidate_review_search_not_finished"
    CANDIDATE_NOT_FOUND = "candidate_review_not_found"
    CANDIDATE_NOT_SELECTABLE = "candidate_review_not_selectable"
    SELECTION_EMPTY = "candidate_review_selection_empty"
    SELECTION_BUSY = "candidate_review_selection_busy"
    INVALID_CURSOR = "candidate_review_invalid_cursor"


class CandidateReviewError(RuntimeError):
    """候选审核的会话、选择或分页前置条件不满足时抛出。"""

    def __init__(self, code: CandidateReviewErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class CandidateReviewSession:
    """维护一个 search run 的审核事实边界，不执行具体页面或批量命令。"""

    def __init__(
        self,
        runs: SearchRunRepository,
        store: SearchSessionStore,
        candidates: SearchCandidateRepository,
    ) -> None:
        self.runs = runs
        self.store = store
        self.candidates = candidates
        self._run_service = SearchRunService(runs)

    async def owned_run(
        self,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> SearchRunRecord:
        return await self._run_service.get_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )

    async def owned_finished_run(
        self,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> SearchRunRecord:
        run = await self.owned_run(owner_user_id, collection_id, search_run_id)
        if run.status not in {
            SearchRunStatus.COMPLETED.value,
            SearchRunStatus.PARTIAL_FAILED.value,
        }:
            raise CandidateReviewError(
                CandidateReviewErrorCode.SEARCH_NOT_FINISHED,
                "文献检索尚未完成，暂时不能开始候选审核。",
            )
        return run

    async def snapshot_candidates(
        self,
        run: SearchRunRecord,
    ) -> tuple[dict[str, object], tuple[UnifiedCandidate, ...]]:
        candidates = await self.candidates.list_candidates(search_run_id=run.id)
        if not candidates:
            raise CandidateReviewError(
                CandidateReviewErrorCode.CANDIDATE_NOT_FOUND,
                "当前检索运行尚无可审核候选。",
            )
        snapshot: dict[str, object] = {
            "status": run.status,
            "stage": run.stage,
            "provider_summary": run.provider_summary,
            "candidate_counts": run.candidate_counts,
        }
        return snapshot, candidates

    @staticmethod
    def visible_candidates(
        candidates: tuple[UnifiedCandidate, ...],
    ) -> tuple[UnifiedCandidate, ...]:
        return tuple(candidate for candidate in candidates if is_screening_candidate(candidate))

    async def synchronize_visible_selection(
        self,
        run: SearchRunRecord,
        selected_ids: set[UUID],
        candidates: tuple[UnifiedCandidate, ...],
    ) -> set[UUID]:
        visible_ids = {candidate.candidate_id for candidate in candidates}
        synchronized = selected_ids & visible_ids
        if synchronized != selected_ids:
            await self.write_selected_ids(run, synchronized)
        return synchronized

    async def selected_ids(self, run: SearchRunRecord) -> set[UUID]:
        return await self.candidates.selected_ids(search_run_id=run.id)

    async def write_selected_ids(
        self,
        run: SearchRunRecord,
        candidate_ids: set[UUID],
    ) -> None:
        current_ids = await self.selected_ids(run)
        removed_ids = current_ids - candidate_ids
        added_ids = candidate_ids - current_ids
        if removed_ids:
            await self.candidates.set_selected(
                search_run_id=run.id,
                candidate_ids=tuple(removed_ids),
                selected=False,
            )
        if added_ids:
            await self.candidates.set_selected(
                search_run_id=run.id,
                candidate_ids=tuple(added_ids),
                selected=True,
            )
        if not candidate_ids and not removed_ids:
            await self.candidates.clear_selection(search_run_id=run.id)
        if run.redis_session_key is not None:
            await self.store.refresh_ttl(run.redis_session_key)

    async def set_selected(
        self,
        run: SearchRunRecord,
        candidate_ids: tuple[UUID, ...],
        *,
        selected: bool,
    ) -> int:
        selected_count = await self.candidates.set_selected(
            search_run_id=run.id,
            candidate_ids=candidate_ids,
            selected=selected,
        )
        if run.redis_session_key is not None:
            await self.store.refresh_ttl(run.redis_session_key)
        return selected_count

    async def fulltext_states(
        self,
        run: SearchRunRecord,
        candidates: tuple[UnifiedCandidate, ...],
    ) -> dict[UUID, CandidateFulltextState]:
        return await self.candidates.list_fulltext_states(
            search_run_id=run.id,
            candidate_ids=tuple(candidate.candidate_id for candidate in candidates),
        )

    @staticmethod
    def session_key(run: SearchRunRecord) -> str:
        if run.redis_session_key is None:
            raise CandidateReviewError(
                CandidateReviewErrorCode.SESSION_EXPIRED,
                "检索候选会话不存在，请重新执行文献检索。",
            )
        return run.redis_session_key

    @staticmethod
    def require_known_selection(
        selected_ids: set[UUID],
        candidates: dict[UUID, UnifiedCandidate],
    ) -> None:
        if any(candidate_id not in candidates for candidate_id in selected_ids):
            raise CandidateReviewError(
                CandidateReviewErrorCode.CANDIDATE_NOT_FOUND,
                "候选准备清单与当前检索结果不一致。",
            )

    @staticmethod
    def require_requested_candidates(
        candidate_ids: list[UUID],
        candidates: dict[UUID, UnifiedCandidate],
        *,
        selected: bool,
    ) -> None:
        for candidate_id in candidate_ids:
            candidate = candidates.get(candidate_id)
            if candidate is None:
                raise CandidateReviewError(
                    CandidateReviewErrorCode.CANDIDATE_NOT_FOUND,
                    "当前检索运行中不存在该候选文献。",
                )
            if not selected:
                continue
            if candidate.triage is None or not candidate.triage.included:
                raise CandidateReviewError(
                    CandidateReviewErrorCode.CANDIDATE_NOT_SELECTABLE,
                    "该候选未通过基础筛选，不能进入研究集合准备清单。",
                )
            if not is_screening_candidate(candidate):
                raise CandidateReviewError(
                    CandidateReviewErrorCode.CANDIDATE_NOT_SELECTABLE,
                    "该候选尚未通过相关性证据核验，不能进入研究集合准备清单。",
                )

    async def require_nonempty_selection(self, run: SearchRunRecord) -> set[UUID]:
        selected_ids = await self.selected_ids(run)
        _snapshot, candidates = await self.snapshot_candidates(run)
        selected_ids = await self.synchronize_visible_selection(
            run,
            selected_ids,
            self.visible_candidates(candidates),
        )
        if not selected_ids:
            raise CandidateReviewError(
                CandidateReviewErrorCode.SELECTION_EMPTY,
                "请先勾选至少一篇候选文献。",
            )
        return selected_ids
