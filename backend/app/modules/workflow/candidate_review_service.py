"""候选审核、跨页准备清单与批量准入编排。

该模块不保存长期候选记录。它只在一个已完成的 ``search_run`` Redis 会话中维护
用户本次审核的准备清单，并复用既有全文和准入服务执行每篇文献的严格处理。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from enum import StrEnum
from uuid import UUID, uuid4

from app.db.models.workflow import SearchRun
from app.modules.collections import (
    CollectionAdmissionError,
    CollectionAdmissionStatus,
    ResearchCollectionAdmissionService,
)
from app.modules.fulltext.contracts import (
    CandidateFulltextState,
    FulltextAcquisitionStatus,
)
from app.modules.fulltext.storage import ResearchDocumentObjectStorage
from app.modules.search.contracts import (
    CandidateRelevanceLevel,
    CandidateRelevanceState,
    UnifiedCandidate,
)
from app.modules.workflow.contracts import (
    CandidateAdmissionBatchResponse,
    CandidateAdmissionItem,
    CandidateFulltextError,
    CandidateFulltextResponse,
    CandidatePreparationBatchResponse,
    CandidatePreparationItem,
    CandidateReviewFilter,
    CandidateSelectionResponse,
    CandidateSelectionSummary,
    SearchCandidatePageInfo,
    SearchCandidatePageResponse,
    SearchCandidateReviewItem,
    SearchRunError,
)
from app.modules.workflow.fulltext_service import CandidateFulltextService
from app.modules.workflow.job_queue import CandidateFulltextJobQueue
from app.modules.workflow.search_run_service import SearchRunService
from app.modules.workflow.search_session import (
    SearchSessionStore,
    build_candidate_fulltext_key,
    build_candidate_selection_key,
    build_candidate_selection_lock_key,
)
from app.modules.workflow.state import SearchRunStatus
from sqlalchemy.ext.asyncio import AsyncSession

_SELECTION_LOCK_TTL_SECONDS = 15
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
    CandidateRelevanceState.FAILED: 6,
    CandidateRelevanceState.SKIPPED: 7,
}


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


class CandidateReviewService:
    """以 Redis 准备清单连接候选审核与既有严格准入服务。"""

    def __init__(
        self,
        session: AsyncSession,
        session_store: SearchSessionStore,
        *,
        fulltext_queue: CandidateFulltextJobQueue | None = None,
        admission_storage: ResearchDocumentObjectStorage | None = None,
    ) -> None:
        """由路由注入请求范围资源，使单元测试可以替换队列和对象存储。"""
        self._session = session
        self._session_store = session_store
        self._fulltext_queue = fulltext_queue
        self._admission_storage = admission_storage

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
        run = await self._owned_run(owner_user_id, collection_id, search_run_id)
        snapshot, candidates = await self._snapshot_and_candidates(run)
        selected_ids = await self._selected_ids(run)
        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        self._require_known_selection(selected_ids, candidate_by_id)
        states = await self._fulltext_states(run, candidates)

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
                candidates,
                final_relevance_order=final_relevance_order,
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
            candidate_counts=candidate_counts,
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
                candidates=candidate_by_id,
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
        run = await self._owned_finished_run(owner_user_id, collection_id, search_run_id)
        _snapshot, candidates = await self._snapshot_and_candidates(run)
        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        selected_ids = await self._selected_ids(run)
        self._require_known_selection(selected_ids, candidate_by_id)
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            raise CandidateReviewError(
                CandidateReviewErrorCode.CANDIDATE_NOT_FOUND,
                "当前检索运行中不存在该候选文献。",
            )
        states = await self._fulltext_states(run, (candidate,))
        return self._review_item(
            candidate,
            is_selected=candidate_id in selected_ids,
            state=states.get(candidate_id),
        )

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
        run = await self._owned_finished_run(owner_user_id, collection_id, search_run_id)
        snapshot, candidates = await self._snapshot_and_candidates(run)
        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        self._require_requested_candidates(candidate_ids, candidate_by_id, selected=selected)
        session_key = self._session_key(run)
        lock_key = build_candidate_selection_lock_key(session_key)
        lock_token = uuid4().hex
        acquired = await self._session_store.try_acquire_lock(
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
            current_ids = await self._selected_ids(run)
            requested = set(candidate_ids)
            updated = current_ids | requested if selected else current_ids - requested
            await self._write_selected_ids(run, updated)
        finally:
            await self._session_store.release_lock(lock_key, token=lock_token)

        return CandidateSelectionResponse(run_id=run.id, selected_count=len(updated))

    async def clear_selection(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> CandidateSelectionResponse:
        """清空当前搜索会话的准备清单，不影响 PostgreSQL 中既有待确认集合。"""
        run = await self._owned_finished_run(owner_user_id, collection_id, search_run_id)
        session_key = self._session_key(run)
        lock_key = build_candidate_selection_lock_key(session_key)
        lock_token = uuid4().hex
        acquired = await self._session_store.try_acquire_lock(
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
            await self._write_selected_ids(run, set())
        finally:
            await self._session_store.release_lock(lock_key, token=lock_token)
        return CandidateSelectionResponse(run_id=run.id, selected_count=0)

    async def prepare_selected(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> CandidatePreparationBatchResponse:
        """为准备清单逐篇投递既有全文任务，题录补齐仍由全文 Worker 负责。"""
        if self._fulltext_queue is None:
            raise RuntimeError("批量全文准备必须提供全文任务队列")

        run = await self._owned_finished_run(owner_user_id, collection_id, search_run_id)
        # Per-candidate rollbacks expire ORM instances. Preserve the scalar needed by the
        # response before entering the independently settled admission loop.
        run_id = run.id
        selected_ids = await self._require_nonempty_selection(run)
        fulltext_service = CandidateFulltextService(
            self._session,
            self._session_store,
            self._fulltext_queue,
        )
        items: list[CandidatePreparationItem] = []
        queued_count = 0

        for candidate_id in sorted(selected_ids, key=str):
            try:
                # 对可重试失败自动创建下一次尝试；活动和可用状态仍由单篇服务幂等返回。
                submission = await fulltext_service.request(
                    owner_user_id=owner_user_id,
                    collection_id=collection_id,
                    search_run_id=search_run_id,
                    candidate_id=candidate_id,
                    retry=True,
                )
                result = submission.state.result
                if result.status in {
                    FulltextAcquisitionStatus.QUEUED,
                    FulltextAcquisitionStatus.DOWNLOADING,
                    FulltextAcquisitionStatus.VALIDATING,
                }:
                    queued_count += 1
                items.append(
                    CandidatePreparationItem(
                        candidate_id=candidate_id,
                        status=result.status,
                        message=self._fulltext_message(
                            result.status,
                            result.error.message if result.error else None,
                        ),
                        retryable=bool(result.error and result.error.retryable),
                    )
                )
            except (CandidateFulltextError, SearchRunError) as exc:
                items.append(
                    CandidatePreparationItem(
                        candidate_id=candidate_id,
                        message=str(exc),
                        retryable=False,
                    )
                )

        return CandidatePreparationBatchResponse(
            run_id=run_id,
            selected_count=len(selected_ids),
            queued_count=queued_count,
            items=items,
        )

    async def admit_selected(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> CandidateAdmissionBatchResponse:
        """把已具备可处理全文的准备候选逐篇纳入待确认集合。"""
        if self._admission_storage is None:
            raise RuntimeError("批量加入集合必须提供对象存储")

        run = await self._owned_finished_run(owner_user_id, collection_id, search_run_id)
        # Per-candidate rollbacks expire ORM instances. Preserve the scalar needed by the
        # response before entering the independently settled admission loop.
        run_id = run.id
        selected_ids = await self._require_nonempty_selection(run)
        fulltext_service = CandidateFulltextService(self._session, self._session_store)
        admission_service = ResearchCollectionAdmissionService(
            self._session,
            self._admission_storage,
        )
        items: list[CandidateAdmissionItem] = []
        succeeded_ids: list[UUID] = []
        admitted_count = 0
        already_joined_count = 0

        for candidate_id in sorted(selected_ids, key=str):
            try:
                submission = await fulltext_service.get_state(
                    owner_user_id=owner_user_id,
                    collection_id=collection_id,
                    search_run_id=search_run_id,
                    candidate_id=candidate_id,
                )
                # get_state 是只读查询，会自动开启事务；准入服务必须拥有独立写事务。
                await self._session.rollback()
                result = await admission_service.admit(
                    owner_user_id=owner_user_id,
                    collection_id=collection_id,
                    candidate=submission.state.candidate,
                    fulltext_result=submission.state.result,
                )
                if result.status is CollectionAdmissionStatus.ADDED:
                    admitted_count += 1
                else:
                    already_joined_count += 1
                succeeded_ids.append(candidate_id)
                items.append(
                    CandidateAdmissionItem(
                        candidate_id=candidate_id,
                        status=result.status.value,
                        message=(
                            "已加入待确认集合。"
                            if result.status is CollectionAdmissionStatus.ADDED
                            else "该文献已在当前集合中。"
                        ),
                    )
                )
            except (CandidateFulltextError, SearchRunError, CollectionAdmissionError) as exc:
                retryable = bool(getattr(exc, "retryable", False))
                items.append(
                    CandidateAdmissionItem(
                        candidate_id=candidate_id,
                        status="blocked",
                        message=str(exc),
                        retryable=retryable,
                    )
                )
            finally:
                # 每篇文献独立结算，失败不允许污染下一篇准入的事务边界。
                await self._session.rollback()

        if succeeded_ids:
            await self.update_selection(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                search_run_id=search_run_id,
                candidate_ids=succeeded_ids,
                selected=False,
            )

        return CandidateAdmissionBatchResponse(
            run_id=run_id,
            selected_count=len(selected_ids),
            admitted_count=admitted_count,
            already_joined_count=already_joined_count,
            blocked_count=len(selected_ids) - len(succeeded_ids),
            items=items,
        )

    async def _owned_run(
        self,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> SearchRun:
        """统一验证搜索运行归属，任何审核操作都不能跨工作区读取候选。"""
        return await SearchRunService(self._session).get_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )

    async def _owned_finished_run(
        self,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> SearchRun:
        """准备和准入只针对来源检索已结束的稳定候选会话。"""
        run = await self._owned_run(owner_user_id, collection_id, search_run_id)
        if run.status not in {
            SearchRunStatus.COMPLETED.value,
            SearchRunStatus.PARTIAL_FAILED.value,
        }:
            raise CandidateReviewError(
                CandidateReviewErrorCode.SEARCH_NOT_FINISHED,
                "文献检索尚未完成，暂时不能开始候选审核。",
            )
        return run

    async def _snapshot_and_candidates(
        self,
        run: SearchRun,
    ) -> tuple[dict[str, object], tuple[UnifiedCandidate, ...]]:
        """读取 Redis 主快照；过期时同步更新 SearchRun 审计状态。"""
        session_key = self._session_key(run)
        snapshot = await self._session_store.read_snapshot(session_key)
        if snapshot is None:
            await SearchRunService(self._session).expire_run(run.id)
            raise CandidateReviewError(
                CandidateReviewErrorCode.SESSION_EXPIRED,
                "检索候选已过期，请重新执行文献检索。",
            )
        raw_candidates = snapshot.get("candidates")
        if not isinstance(raw_candidates, list):
            raise CandidateReviewError(
                CandidateReviewErrorCode.SESSION_EXPIRED,
                "检索候选快照格式无效，请重新执行文献检索。",
            )
        return snapshot, tuple(UnifiedCandidate.model_validate(item) for item in raw_candidates)

    async def _selected_ids(self, run: SearchRun) -> set[UUID]:
        """读取 Redis 准备清单；不存在时代表用户还未选择候选。"""
        snapshot = await self._session_store.read_snapshot(
            build_candidate_selection_key(self._session_key(run))
        )
        if snapshot is None:
            return set()
        raw_ids = snapshot.get("candidate_ids")
        if not isinstance(raw_ids, list) or not all(isinstance(value, str) for value in raw_ids):
            raise CandidateReviewError(
                CandidateReviewErrorCode.SESSION_EXPIRED,
                "候选准备清单格式无效，请重新执行文献检索。",
            )
        try:
            return {UUID(value) for value in raw_ids}
        except ValueError as exc:
            raise CandidateReviewError(
                CandidateReviewErrorCode.SESSION_EXPIRED,
                "候选准备清单格式无效，请重新执行文献检索。",
            ) from exc

    async def _write_selected_ids(self, run: SearchRun, candidate_ids: set[UUID]) -> None:
        """持久化短期选择并只刷新主快照 TTL，不回写其 JSON 内容。"""
        session_key = self._session_key(run)
        await self._session_store.write_snapshot(
            build_candidate_selection_key(session_key),
            {
                "candidate_ids": [
                    str(candidate_id) for candidate_id in sorted(candidate_ids, key=str)
                ]
            },
        )
        await self._session_store.refresh_ttl(session_key)

    async def _fulltext_states(
        self,
        run: SearchRun,
        candidates: tuple[UnifiedCandidate, ...],
    ) -> dict[UUID, CandidateFulltextState]:
        """批量读取候选全文状态，候选主快照与全文状态仍保持独立生命周期。"""
        session_key = self._session_key(run)
        state_keys = [
            build_candidate_fulltext_key(session_key, candidate.candidate_id)
            for candidate in candidates
        ]
        raw_states = await self._session_store.read_many_snapshots(state_keys)
        states: dict[UUID, CandidateFulltextState] = {}
        for candidate in candidates:
            state_key = build_candidate_fulltext_key(session_key, candidate.candidate_id)
            raw_state = raw_states.get(state_key)
            if raw_state is not None:
                states[candidate.candidate_id] = CandidateFulltextState.model_validate(raw_state)
        return states

    @staticmethod
    def _session_key(run: SearchRun) -> str:
        """Redis 会话键只能来自持久化 SearchRun，不能由 URL 参数伪造。"""
        if run.redis_session_key is None:
            raise CandidateReviewError(
                CandidateReviewErrorCode.SESSION_EXPIRED,
                "检索候选会话不存在，请重新执行文献检索。",
            )
        return run.redis_session_key

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
                CandidateReviewService._final_relevance_rank(candidate),
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
    def _uses_final_relevance_order(run: SearchRun) -> bool:
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
        """所有可见筛选都基于服务端统一候选与全文状态，而不是前端关键词推测。"""
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
        if review_filter is CandidateReviewFilter.NEEDS_REVIEW:
            return not is_priority and level is not CandidateRelevanceLevel.BACKGROUND
        if review_filter is CandidateReviewFilter.AVAILABLE:
            return state is not None and state.result.status is FulltextAcquisitionStatus.AVAILABLE
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
        """全文 Worker 已补齐题录时优先展示其候选快照，避免旧主快照误报未核验。"""
        return SearchCandidateReviewItem(
            candidate=state.candidate if state is not None else candidate,
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
        candidates: dict[UUID, UnifiedCandidate],
        states: dict[UUID, CandidateFulltextState],
    ) -> CandidateSelectionSummary:
        """将准备清单按下一步操作分流，前端无需自行推断批量按钮可用性。"""
        needs_fulltext_count = 0
        fulltext_in_progress_count = 0
        ready_for_admission_count = 0
        blocked_count = 0
        for candidate_id in selected_ids:
            candidate = candidates[candidate_id]
            state = states.get(candidate_id)
            if candidate.doi is None:
                blocked_count += 1
            elif state is None:
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
    def _require_known_selection(
        selected_ids: set[UUID],
        candidates: dict[UUID, UnifiedCandidate],
    ) -> None:
        """防止失配的准备清单在候选快照变化后静默指向其他论文。"""
        if any(candidate_id not in candidates for candidate_id in selected_ids):
            raise CandidateReviewError(
                CandidateReviewErrorCode.SESSION_EXPIRED,
                "候选准备清单与当前检索结果不一致，请重新执行文献检索。",
            )

    @staticmethod
    def _require_requested_candidates(
        candidate_ids: list[UUID],
        candidates: dict[UUID, UnifiedCandidate],
        *,
        selected: bool,
    ) -> None:
        """选择操作仅接受当前服务端会话中的可入 RAG 候选。"""
        for candidate_id in candidate_ids:
            candidate = candidates.get(candidate_id)
            if candidate is None:
                raise CandidateReviewError(
                    CandidateReviewErrorCode.CANDIDATE_NOT_FOUND,
                    "当前检索运行中不存在该候选文献。",
                )
            if not selected:
                continue
            if candidate.doi is None:
                raise CandidateReviewError(
                    CandidateReviewErrorCode.CANDIDATE_NOT_SELECTABLE,
                    "该候选缺少 DOI，可人工查看但不能进入研究集合准备清单。",
                )
            # 全文服务也要求候选已通过基础初筛；缺失初筛结果不能被当作默认通过。
            if candidate.triage is None or not candidate.triage.included:
                raise CandidateReviewError(
                    CandidateReviewErrorCode.CANDIDATE_NOT_SELECTABLE,
                    "该候选未通过基础筛选，不能进入研究集合准备清单。",
                )

    async def _require_nonempty_selection(self, run: SearchRun) -> set[UUID]:
        """批量操作不接受空清单，避免产生没有业务意义的队列任务。"""
        selected_ids = await self._selected_ids(run)
        if not selected_ids:
            raise CandidateReviewError(
                CandidateReviewErrorCode.SELECTION_EMPTY,
                "请先勾选至少一篇带 DOI 的候选文献。",
            )
        return selected_ids

    @staticmethod
    def _filter_fingerprint(
        *,
        query: str,
        review_filter: CandidateReviewFilter,
        limit: int,
        sort_version: str,
    ) -> str:
        """把游标绑定到筛选与排序语义，防止旧游标穿透到不同结果集。"""
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
        """校验游标结构与当前筛选上下文，拒绝任意偏移量注入。"""
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

    @staticmethod
    def _fulltext_message(status: FulltextAcquisitionStatus, error_message: str | None) -> str:
        """批量操作只承诺实际状态，不把任务投递说成全文已经可用。"""
        if error_message:
            return error_message
        if status is FulltextAcquisitionStatus.AVAILABLE:
            return "全文已核验，可加入待确认集合。"
        if status in {
            FulltextAcquisitionStatus.QUEUED,
            FulltextAcquisitionStatus.DOWNLOADING,
            FulltextAcquisitionStatus.VALIDATING,
        }:
            return "题录与全文核验已安排，结果会在本页更新。"
        return "全文暂时无法用于研究集合。"
