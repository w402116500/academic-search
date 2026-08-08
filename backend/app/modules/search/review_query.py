"""候选审核查询用例。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import cast
from uuid import UUID

from app.modules.documents.api_contracts import CandidateFulltextResponse
from app.modules.documents.contracts import CandidateFulltextState, FulltextAcquisitionStatus
from app.modules.search.api_contracts import (
    CandidateCounts,
    CandidateReviewFilter,
    CandidateSelectionSummary,
    SearchCandidatePageInfo,
    SearchCandidatePageResponse,
    SearchCandidateReviewItem,
)
from app.modules.search.contracts import (
    CandidateRelevanceLevel,
    CandidateRelevanceState,
    UnifiedCandidate,
)
from app.modules.search.relevance import is_screening_candidate
from app.modules.search.review_session import (
    CandidateReviewError,
    CandidateReviewErrorCode,
    CandidateReviewSession,
)
from app.modules.search.run_models import SearchRunRecord
from app.modules.search.state import SearchRunStatus

_DISCOVERY_SORT_VERSION = "discovery-v1"
_RELEVANCE_SORT_VERSION = "relevance-v1"
_RELEVANCE_LEVEL_RANK = {
    CandidateRelevanceLevel.CORE: 0,
    CandidateRelevanceLevel.RELATED: 1,
    CandidateRelevanceLevel.BACKGROUND: 2,
    CandidateRelevanceLevel.NOT_RECOMMENDED: 3,
    CandidateRelevanceLevel.INSUFFICIENT_INFORMATION: 4,
}
_INCOMPLETE_RELEVANCE_STATE_RANK = {
    CandidateRelevanceState.PENDING: 5,
    CandidateRelevanceState.EXCLUDED: 6,
    CandidateRelevanceState.FAILED: 6,
    CandidateRelevanceState.SKIPPED: 7,
}


class CandidateReviewQueryService:
    """读取候选审核页和单篇检查器，不修改准备清单。"""

    def __init__(self, session: CandidateReviewSession) -> None:
        self._session = session

    async def page(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        limit: int,
        cursor: str | None,
        query: str,
        review_filter: CandidateReviewFilter,
    ) -> SearchCandidatePageResponse:
        """返回当前候选审核页及跨页准备清单摘要。"""
        run = await self._session.owned_run(owner_user_id, collection_id, search_run_id)
        snapshot, all_candidates = await self._session.snapshot_candidates(run)
        candidates = self._session.visible_candidates(all_candidates)
        selected_ids = await self._session.selected_ids(run)
        selected_ids = await self._session.synchronize_visible_selection(
            run, selected_ids, candidates
        )
        states = await self._session.fulltext_states(run, candidates)

        normalized_query = " ".join(query.split()).casefold()
        final_relevance_order = self._uses_final_relevance_order(run)
        sort_version = _RELEVANCE_SORT_VERSION if final_relevance_order else _DISCOVERY_SORT_VERSION
        fingerprint = self._filter_fingerprint(
            query=normalized_query,
            review_filter=review_filter,
            limit=limit,
            sort_version=sort_version,
        )
        offset = self._decode_cursor(cursor, expected_fingerprint=fingerprint)
        filtered = [
            candidate
            for candidate in self._stable_sorted(
                candidates, final_relevance_order=final_relevance_order
            )
            if self._matches_filter(
                candidate,
                state=states.get(candidate.candidate_id),
                selected_ids=selected_ids,
                query=normalized_query,
                review_filter=review_filter,
            )
        ]
        page_candidates = filtered[offset : offset + limit]
        next_offset = offset + len(page_candidates)
        next_cursor = (
            self._encode_cursor(offset=next_offset, fingerprint=fingerprint)
            if next_offset < len(filtered)
            else None
        )

        candidate_counts = snapshot.get("candidate_counts", {})
        if not isinstance(candidate_counts, dict):
            raise CandidateReviewError(
                CandidateReviewErrorCode.SESSION_EXPIRED,
                "检索候选快照缺少处理统计，请重新执行文献检索。",
            )

        return SearchCandidatePageResponse(
            run_id=run.id,
            status=SearchRunStatus(snapshot.get("status", run.status)),
            candidate_counts=cast(CandidateCounts, candidate_counts),
            items=[
                self._review_item(
                    candidate,
                    is_selected=candidate.candidate_id in selected_ids,
                    state=states.get(candidate.candidate_id),
                )
                for candidate in page_candidates
            ],
            page=SearchCandidatePageInfo(
                limit=limit,
                total=len(filtered),
                next_cursor=next_cursor,
            ),
            selection=self._selection_summary(
                selected_ids=selected_ids,
                states=states,
            ),
        )

    async def item(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        candidate_id: UUID,
    ) -> SearchCandidateReviewItem:
        """读取一篇候选的审核视图，详情页不依赖当前分页或浏览器缓存。"""
        run = await self._session.owned_finished_run(owner_user_id, collection_id, search_run_id)
        _snapshot, all_candidates = await self._session.snapshot_candidates(run)
        candidates = self._session.visible_candidates(all_candidates)
        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        selected_ids = await self._session.selected_ids(run)
        selected_ids = await self._session.synchronize_visible_selection(
            run, selected_ids, candidates
        )
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            raise CandidateReviewError(
                CandidateReviewErrorCode.CANDIDATE_NOT_FOUND,
                "当前检索运行中不存在该候选文献。",
            )
        states = await self._session.fulltext_states(run, (candidate,))
        return self._review_item(
            candidate,
            is_selected=candidate_id in selected_ids,
            state=states.get(candidate_id),
        )

    @staticmethod
    def _stable_sorted(
        candidates: tuple[UnifiedCandidate, ...],
        *,
        final_relevance_order: bool,
    ) -> list[UnifiedCandidate]:
        """运行中保持发现顺序；终态按相关性优先且使用稳定辅助信号。"""
        if not final_relevance_order:
            return sorted(
                candidates,
                key=lambda candidate: (
                    -(candidate.published_year or 0),
                    candidate.title_key.casefold(),
                    str(candidate.candidate_id),
                ),
            )
        return sorted(
            candidates,
            key=lambda candidate: (
                CandidateReviewQueryService._final_relevance_rank(candidate),
                -len(candidate.source_records),
                candidate.is_open_access is not True,
                -(candidate.published_year or 0),
                candidate.title_key.casefold(),
                str(candidate.candidate_id),
            ),
        )

    @staticmethod
    def _final_relevance_rank(candidate: UnifiedCandidate) -> int:
        """终态将语义层级置顶；待评估、失败、跳过记录明确放在所有层级之后。"""
        if (
            candidate.relevance_state is CandidateRelevanceState.COMPLETED
            and candidate.relevance_assessment is not None
        ):
            return _RELEVANCE_LEVEL_RANK[candidate.relevance_assessment.level]
        return _INCOMPLETE_RELEVANCE_STATE_RANK.get(candidate.relevance_state, 6)

    @staticmethod
    def _uses_final_relevance_order(run: SearchRunRecord) -> bool:
        """只有可审核终态切换为相关性排序，运行中游标继续使用发现顺序。"""
        return run.status in {
            SearchRunStatus.COMPLETED.value,
            SearchRunStatus.PARTIAL_FAILED.value,
        }

    @staticmethod
    def _matches_filter(
        candidate: UnifiedCandidate,
        *,
        state: CandidateFulltextState | None,
        selected_ids: set[UUID],
        query: str,
        review_filter: CandidateReviewFilter,
    ) -> bool:
        """所有可见筛选都基于服务端统一候选与全文状态。"""
        if not is_screening_candidate(candidate):
            return False
        if query:
            searchable = " ".join(
                (candidate.title, *(author.name for author in candidate.authors))
            ).casefold()
            if query not in searchable:
                return False

        level = candidate.relevance_assessment.level if candidate.relevance_assessment else None
        is_priority = level in {CandidateRelevanceLevel.CORE, CandidateRelevanceLevel.RELATED}
        if review_filter is CandidateReviewFilter.ALL:
            return True
        if review_filter is CandidateReviewFilter.CHINESE:
            return candidate.language.value == "zh"
        if review_filter is CandidateReviewFilter.ENGLISH:
            return candidate.language.value == "en"
        if review_filter is CandidateReviewFilter.PRIORITY:
            return is_priority
        if review_filter is CandidateReviewFilter.BACKGROUND:
            return level is CandidateRelevanceLevel.BACKGROUND
        if review_filter is CandidateReviewFilter.AVAILABLE:
            return (
                candidate.pdf_availability is not None
                and candidate.pdf_availability.status.value == "available"
            )
        if review_filter is CandidateReviewFilter.OPEN_ACCESS:
            return candidate.is_open_access is True
        if review_filter is CandidateReviewFilter.HAS_DOI:
            return candidate.doi is not None
        return candidate.candidate_id in selected_ids

    @staticmethod
    def _review_item(
        candidate: UnifiedCandidate,
        *,
        is_selected: bool,
        state: CandidateFulltextState | None,
    ) -> SearchCandidateReviewItem:
        """全文 Worker 已补齐题录时优先展示其候选快照。"""
        displayed_candidate = candidate
        if state is not None and state.candidate.citation is not None:
            displayed_candidate = candidate.model_copy(
                update={"citation": state.candidate.citation}
            )
        return SearchCandidateReviewItem(
            candidate=displayed_candidate,
            is_selected=is_selected,
            fulltext=(
                CandidateFulltextResponse(
                    search_run_id=state.search_run_id,
                    candidate_id=state.candidate.candidate_id,
                    attempt_no=state.attempt_no,
                    status=state.result.status,
                    document=state.result.document,
                    error=state.result.error,
                    requested_at=state.requested_at,
                    updated_at=state.updated_at,
                )
                if state is not None
                else None
            ),
        )

    @staticmethod
    def _selection_summary(
        *,
        selected_ids: set[UUID],
        states: dict[UUID, CandidateFulltextState],
    ) -> CandidateSelectionSummary:
        """将准备清单按下一步操作分流，前端无需自行推断批量按钮。"""
        needs_fulltext_count = 0
        fulltext_in_progress_count = 0
        ready_for_admission_count = 0
        blocked_count = 0
        for candidate_id in selected_ids:
            state = states.get(candidate_id)
            if state is None:
                needs_fulltext_count += 1
            elif state.result.status in {
                FulltextAcquisitionStatus.QUEUED,
                FulltextAcquisitionStatus.DOWNLOADING,
                FulltextAcquisitionStatus.VALIDATING,
            }:
                fulltext_in_progress_count += 1
            elif state.result.status is FulltextAcquisitionStatus.AVAILABLE:
                ready_for_admission_count += 1
            else:
                blocked_count += 1
        return CandidateSelectionSummary(
            selected_count=len(selected_ids),
            needs_fulltext_count=needs_fulltext_count,
            fulltext_in_progress_count=fulltext_in_progress_count,
            ready_for_admission_count=ready_for_admission_count,
            blocked_count=blocked_count,
        )

    @staticmethod
    def _filter_fingerprint(
        *,
        query: str,
        review_filter: CandidateReviewFilter,
        limit: int,
        sort_version: str,
    ) -> str:
        """把游标绑定到筛选与排序语义。"""
        value = json.dumps(
            {
                "query": query,
                "filter": review_filter.value,
                "limit": limit,
                "sort_version": sort_version,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _encode_cursor(*, offset: int, fingerprint: str) -> str:
        """生成不向用户暴露候选排序字段的透明游标。"""
        payload = json.dumps({"offset": offset, "fingerprint": fingerprint}, separators=(",", ":"))
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None, *, expected_fingerprint: str) -> int:
        """校验游标结构与当前筛选上下文。"""
        if cursor is None:
            return 0
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            offset = payload["offset"]
            fingerprint = payload["fingerprint"]
        except (
            binascii.Error,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise CandidateReviewError(
                CandidateReviewErrorCode.INVALID_CURSOR,
                "候选分页游标无效，请返回第一页重新审核。",
            ) from exc
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or fingerprint != expected_fingerprint
        ):
            raise CandidateReviewError(
                CandidateReviewErrorCode.INVALID_CURSOR,
                "候选分页游标与当前筛选条件不匹配，请返回第一页重新审核。",
            )
        return offset
