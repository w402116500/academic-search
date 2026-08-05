"""独立 Worker 执行完整候选集合的流式相关性分析。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any
from uuid import UUID, uuid4

from app.modules.research.query_plan import read_confirmed_query_plan
from app.modules.search.api_contracts import SearchProgressEvent
from app.modules.search.citation_enrichment import CitationMetadataEnricher
from app.modules.search.contracts import (
    CandidateRelevanceState,
    UnifiedCandidate,
)
from app.modules.search.queue import CandidateRelevanceJobQueue, CandidateRelevanceQueueError
from app.modules.search.relevance import (
    CandidateRelevanceEvaluator,
    CandidateRelevanceTechnicalFailure,
    build_candidate_relevance_context,
    exclude_candidate_relevance,
    is_screening_candidate,
)
from app.modules.search.run_models import SearchRunRecord
from app.modules.search.run_repository import SearchRunRepository
from app.modules.search.run_service import SearchRunService
from app.modules.search.session import (
    SearchSessionStore,
    build_candidate_relevance_lock_key,
    build_candidate_selection_key,
)
from app.modules.search.state import SearchRunStage, SearchRunStatus

logger = logging.getLogger(__name__)

_LEASE_TTL_SECONDS = 90
_LEASE_HEARTBEAT_SECONDS = 30


class CandidateRelevanceRunExecutor:
    """以完整集合流式调用模型，并通过 Redis 合并保护其他候选更新。"""

    def __init__(
        self,
        *,
        runs: SearchRunRepository,
        search_run_id: UUID,
        session_store: SearchSessionStore,
        citation_enrichment_limit: int,
        citation_enricher: CitationMetadataEnricher | None,
        attempt_no: int = 1,
        relevance_queue: CandidateRelevanceJobQueue | None = None,
        evaluator: CandidateRelevanceEvaluator | None = None,
    ) -> None:
        if citation_enrichment_limit < 0:
            raise ValueError("候选题录预取限额不能为负数。")
        self._runs = runs
        self._search_run_id = search_run_id
        self._session_store = session_store
        self._citation_enrichment_limit = citation_enrichment_limit
        self._citation_enricher = citation_enricher
        self._attempt_no = attempt_no
        self._relevance_queue = relevance_queue
        self._evaluator = evaluator
        self._workflow_service = SearchRunService(runs)

    async def execute(self, *, arq_context: dict[str, Any]) -> dict[str, str]:
        """执行或安全忽略过期、重复的 relevance 队列消息。"""
        run = await self._runs.get_relevance_run_for_update(self._search_run_id)
        if run is None:
            return {"search_run_id": str(self._search_run_id), "status": "ignored"}
        if (
            run.status != SearchRunStatus.RUNNING.value
            or run.stage != SearchRunStage.RELEVANCE_ASSESSMENT.value
            or run.redis_session_key is None
        ):
            return {"search_run_id": str(self._search_run_id), "status": "ignored"}

        session_key = run.redis_session_key
        lock_key = build_candidate_relevance_lock_key(session_key)
        lock_token = uuid4().hex
        if not await self._session_store.try_acquire_lock(
            lock_key,
            token=lock_token,
            ttl_seconds=_LEASE_TTL_SECONDS,
        ):
            return {"search_run_id": str(self._search_run_id), "status": "already_running"}

        if not await self._renew_leases_once(
            session_key=session_key,
            lock_key=lock_key,
            lock_token=lock_token,
            arq_job_id=arq_context.get("job_id"),
        ):
            await self._session_store.release_lock(lock_key, token=lock_token)
            return {"search_run_id": str(self._search_run_id), "status": "lease_lost"}

        heartbeat = asyncio.create_task(
            self._renew_leases(
                session_key=session_key,
                lock_key=lock_key,
                lock_token=lock_token,
                arq_job_id=arq_context.get("job_id"),
            )
        )
        try:
            return await self._execute_locked(run, session_key)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            await self._session_store.release_lock(lock_key, token=lock_token)

    async def _execute_locked(self, run: SearchRunRecord, session_key: str) -> dict[str, str]:
        snapshot = await self._session_store.read_snapshot(session_key)
        if snapshot is None:
            await self._workflow_service.complete_run(
                search_run_id=run.id,
                status=SearchRunStatus.FAILED,
                provider_summary=run.provider_summary,
                candidate_counts=run.candidate_counts,
                error_code="candidate_relevance_session_expired",
                error_message="检索候选会话已过期，无法继续相关性分析。",
            )
            return {"search_run_id": str(run.id), "status": "expired"}
        snapshot_attempt_no = snapshot.get("relevance_attempt_no", 1)
        if not isinstance(snapshot_attempt_no, int) or snapshot_attempt_no != self._attempt_no:
            return {"search_run_id": str(run.id), "status": "stale_attempt"}
        candidates = self._deserialize_candidates(snapshot)
        included = tuple(
            candidate
            for candidate in candidates
            if candidate.triage is not None and candidate.triage.included
        )
        try:
            assessed = await self._assess_collection(run, included)
        except CandidateRelevanceTechnicalFailure as failure:
            if await self._retry_technical_failure(
                run=run,
                session_key=session_key,
                failure=failure,
            ):
                return {"search_run_id": str(run.id), "status": "retry_queued"}
            logger.error(
                "Candidate relevance attempts exhausted: run_id=%s code=%s",
                run.id,
                failure.code,
            )
            assessed = self._exclude_pending_candidates(
                included,
                code=failure.code,
                message="候选相关性无法形成可靠结论，当前候选不会进入筛选。",
            )

        merged = await self._session_store.merge_snapshot(
            session_key,
            lambda current: self._merge_relevance(current, assessed),
        )
        merged = await self._session_store.merge_snapshot(
            session_key,
            lambda current: self._set_snapshot_stage(current, SearchRunStage.CITATION_ENRICHMENT),
        )
        merged_candidates = self._deserialize_candidates(merged)
        await self._prune_selection(run, merged_candidates)
        merged_counts = self._candidate_counts(merged, merged_candidates)
        await self._workflow_service.update_progress(
            search_run_id=run.id,
            stage=SearchRunStage.CITATION_ENRICHMENT,
            provider_summary=merged.get("provider_summary", run.provider_summary),
            candidate_counts=merged_counts,
        )
        await self._publish(
            run=run,
            snapshot=merged,
            stage=SearchRunStage.CITATION_ENRICHMENT,
            message="相关性理由已完成核验，正在补全优先候选的正式题录。",
        )

        enriched = await self._enrich_citations(merged_candidates)
        final_snapshot = await self._session_store.merge_snapshot(
            session_key,
            lambda current: self._merge_citations(current, enriched),
        )
        final_candidates = self._deserialize_candidates(final_snapshot)
        final_counts = self._candidate_counts(final_snapshot, final_candidates)
        final_counts["citation_enriched_count"] = sum(
            candidate.citation is not None for candidate in final_candidates
        )
        final_status, error_code, error_message = self._final_status(
            final_snapshot.get("provider_summary", run.provider_summary),
            final_counts,
        )
        completed = await self._workflow_service.complete_run(
            search_run_id=run.id,
            status=final_status,
            provider_summary=final_snapshot.get("provider_summary", run.provider_summary),
            candidate_counts=final_counts,
            error_code=error_code,
            error_message=error_message,
        )
        final_snapshot = await self._session_store.merge_snapshot(
            session_key,
            lambda current: self._finish_snapshot(
                current,
                status=final_status,
                candidate_counts=final_counts,
            ),
        )
        await self._publish(
            run=completed or run,
            snapshot=final_snapshot,
            stage=SearchRunStage.COMPLETED,
            message=error_message or "检索完成，候选文献已准备好。",
        )
        return {"search_run_id": str(run.id), "status": final_status.value}

    async def _assess_collection(
        self,
        run: SearchRunRecord,
        candidates: tuple[UnifiedCandidate, ...],
    ) -> tuple[UnifiedCandidate, ...]:
        """一次输入完整已纳入集合；技术性错误交给 Worker 统一重投。"""
        if not candidates:
            return ()
        plan = await self._runs.get_plan(run.research_plan_id)
        if plan is None:
            raise CandidateRelevanceTechnicalFailure(
                "candidate_relevance_plan_missing",
                "检索计划已不存在，无法生成候选理由。",
            )
        try:
            query_specs, scope = read_confirmed_query_plan(plan)
            context = build_candidate_relevance_context(
                research_question=plan.raw_request,
                direction_options=plan.direction_options,
                selected_direction_id=plan.selected_direction_id,
                query_specs=query_specs,
                scope=scope,
            )
            if self._evaluator is None:
                raise RuntimeError("候选相关性评估器尚未装配。")
            return await self._evaluator.assess(
                context=context,
                candidates=candidates,
            )
        except CandidateRelevanceTechnicalFailure:
            raise
        except Exception as exc:
            logger.exception(
                "Candidate relevance execution failed before a complete validated response: "
                "run_id=%s candidate_count=%s",
                run.id,
                len(candidates),
            )
            raise CandidateRelevanceTechnicalFailure(
                "candidate_relevance_model_unavailable",
                "候选相关性模型暂时不可用。",
            ) from exc

    @staticmethod
    def _exclude_pending_candidates(
        candidates: tuple[UnifiedCandidate, ...],
        *,
        code: str,
        message: str,
    ) -> tuple[UnifiedCandidate, ...]:
        return tuple(
            exclude_candidate_relevance(candidate, message, code=code)
            if candidate.relevance_state is CandidateRelevanceState.PENDING
            else candidate
            for candidate in candidates
        )

    @staticmethod
    def _deserialize_candidates(snapshot: dict[str, Any]) -> tuple[UnifiedCandidate, ...]:
        raw_candidates = snapshot.get("candidates")
        if not isinstance(raw_candidates, list):
            raise ValueError("检索候选快照格式无效")
        return tuple(UnifiedCandidate.model_validate(item) for item in raw_candidates)

    @staticmethod
    def _candidate_counts(
        snapshot: dict[str, Any],
        candidates: tuple[UnifiedCandidate, ...],
    ) -> dict[str, Any]:
        existing = snapshot.get("candidate_counts")
        counts = dict(existing) if isinstance(existing, dict) else {}
        counts["relevance_total_count"] = sum(
            candidate.triage is not None and candidate.triage.included for candidate in candidates
        )
        for key in (
            "relevance_pending_count",
            "relevance_completed_count",
            "relevance_insufficient_count",
            "relevance_failed_count",
        ):
            counts.pop(key, None)
        counts["relevance_analyzed_count"] = sum(
            candidate.triage is not None
            and candidate.triage.included
            and candidate.relevance_state
            in {CandidateRelevanceState.COMPLETED, CandidateRelevanceState.EXCLUDED}
            for candidate in candidates
        )
        counts["relevance_excluded_count"] = sum(
            candidate.triage is not None
            and candidate.triage.included
            and candidate.relevance_state is CandidateRelevanceState.EXCLUDED
            for candidate in candidates
        )
        counts["screening_candidate_count"] = sum(
            is_screening_candidate(candidate) for candidate in candidates
        )
        return counts

    async def _retry_technical_failure(
        self,
        *,
        run: SearchRunRecord,
        session_key: str,
        failure: CandidateRelevanceTechnicalFailure,
    ) -> bool:
        """只对同一完整候选集合自动补跑一次，浏览器始终只看到正常分析进度。"""
        if self._attempt_no >= 2 or self._relevance_queue is None:
            return False

        next_attempt_no = self._attempt_no + 1
        snapshot = await self._session_store.merge_snapshot(
            session_key,
            lambda current: self._schedule_retry_snapshot(current, next_attempt_no),
        )
        candidate_counts = snapshot.get("candidate_counts", {})
        if not isinstance(candidate_counts, dict):
            return False
        await self._workflow_service.update_progress(
            search_run_id=run.id,
            stage=SearchRunStage.RELEVANCE_ASSESSMENT,
            provider_summary=snapshot.get("provider_summary", run.provider_summary),
            candidate_counts=candidate_counts,
        )
        try:
            await self._relevance_queue.enqueue_relevance(
                search_run_id=run.id,
                attempt_no=next_attempt_no,
            )
        except CandidateRelevanceQueueError:
            logger.exception(
                "Candidate relevance automatic retry could not be queued: run_id=%s code=%s",
                run.id,
                failure.code,
            )
            return False
        await self._publish(
            run=run,
            snapshot=snapshot,
            stage=SearchRunStage.RELEVANCE_ASSESSMENT,
            message="正在分析候选相关性。",
        )
        return True

    @staticmethod
    def _schedule_retry_snapshot(snapshot: dict[str, Any], attempt_no: int) -> dict[str, Any]:
        updated = dict(snapshot)
        updated["status"] = SearchRunStatus.RUNNING.value
        updated["stage"] = SearchRunStage.RELEVANCE_ASSESSMENT.value
        updated["relevance_attempt_no"] = attempt_no
        return updated

    async def _prune_selection(
        self,
        run: SearchRunRecord,
        candidates: tuple[UnifiedCandidate, ...],
    ) -> None:
        """相关性结束后移除旧快照中已不再进入筛选的准备清单项。"""
        session_key = run.redis_session_key
        if session_key is None:
            return
        selected_key = build_candidate_selection_key(session_key)
        selection = await self._session_store.read_snapshot(selected_key)
        if selection is None:
            return
        raw_ids = selection.get("candidate_ids")
        if not isinstance(raw_ids, list) or not all(isinstance(value, str) for value in raw_ids):
            return
        allowed = {
            str(candidate.candidate_id)
            for candidate in candidates
            if is_screening_candidate(candidate)
        }
        kept_ids = [candidate_id for candidate_id in raw_ids if candidate_id in allowed]
        if kept_ids == raw_ids:
            return
        await self._session_store.write_snapshot(selected_key, {"candidate_ids": kept_ids})

    @staticmethod
    def _merge_relevance(
        snapshot: dict[str, Any],
        assessed: tuple[UnifiedCandidate, ...],
    ) -> dict[str, Any]:
        """仅覆盖 relevance 字段，保留并发写入的题录、全文和选择状态。"""
        candidates = CandidateRelevanceRunExecutor._deserialize_candidates(snapshot)
        assessed_by_id = {candidate.candidate_id: candidate for candidate in assessed}
        merged_candidates = tuple(
            candidate.model_copy(
                update={
                    "relevance_state": replacement.relevance_state,
                    "relevance_assessment": replacement.relevance_assessment,
                    "relevance_error": replacement.relevance_error,
                }
            )
            if (replacement := assessed_by_id.get(candidate.candidate_id)) is not None
            else candidate
            for candidate in candidates
        )
        merged = dict(snapshot)
        merged["status"] = SearchRunStatus.RUNNING.value
        merged["stage"] = SearchRunStage.RELEVANCE_ASSESSMENT.value
        merged["candidate_counts"] = CandidateRelevanceRunExecutor._candidate_counts(
            merged, merged_candidates
        )
        merged["candidates"] = [
            candidate.model_dump(mode="json") for candidate in merged_candidates
        ]
        return merged

    @staticmethod
    def _merge_citations(
        snapshot: dict[str, Any],
        enriched: tuple[UnifiedCandidate, ...],
    ) -> dict[str, Any]:
        """题录预取完成后同样只叠加 citation 字段。"""
        candidates = CandidateRelevanceRunExecutor._deserialize_candidates(snapshot)
        enriched_by_id = {candidate.candidate_id: candidate for candidate in enriched}
        merged_candidates = tuple(
            candidate.model_copy(update={"citation": replacement.citation})
            if (replacement := enriched_by_id.get(candidate.candidate_id)) is not None
            else candidate
            for candidate in candidates
        )
        merged = dict(snapshot)
        merged["candidates"] = [
            candidate.model_dump(mode="json") for candidate in merged_candidates
        ]
        return merged

    @staticmethod
    def _finish_snapshot(
        snapshot: dict[str, Any],
        *,
        status: SearchRunStatus,
        candidate_counts: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(snapshot)
        merged["status"] = status.value
        merged["stage"] = SearchRunStage.COMPLETED.value
        merged["candidate_counts"] = candidate_counts
        return merged

    @staticmethod
    def _set_snapshot_stage(
        snapshot: dict[str, Any],
        stage: SearchRunStage,
    ) -> dict[str, Any]:
        merged = dict(snapshot)
        merged["stage"] = stage.value
        return merged

    async def _enrich_citations(
        self,
        candidates: tuple[UnifiedCandidate, ...],
    ) -> tuple[UnifiedCandidate, ...]:
        limit = self._citation_enrichment_limit
        if limit == 0:
            return candidates
        enricher = self._citation_enricher
        if enricher is None:
            raise RuntimeError("候选题录补全器尚未装配。")
        included_ids = [
            candidate.candidate_id
            for candidate in candidates
            if candidate.triage is not None
            and candidate.triage.included
            and is_screening_candidate(candidate)
        ]
        selected_ids = set(included_ids[:limit])
        semaphore = asyncio.Semaphore(8)

        async def enrich(candidate: UnifiedCandidate) -> UnifiedCandidate:
            if candidate.candidate_id not in selected_ids:
                return candidate
            async with semaphore:
                return await enricher.enrich(candidate)

        return tuple(await asyncio.gather(*(enrich(candidate) for candidate in candidates)))

    @staticmethod
    def _final_status(
        provider_summary: Any,
        candidate_counts: dict[str, Any],
    ) -> tuple[SearchRunStatus, str | None, str | None]:
        providers = provider_summary if isinstance(provider_summary, dict) else {}
        statuses = [
            str(summary.get("status", ""))
            for summary in providers.values()
            if isinstance(summary, dict)
        ]
        provider_success = any(status != "failed" for status in statuses)
        provider_failure = any(status in {"failed", "partial"} for status in statuses)
        if not provider_success:
            return (
                SearchRunStatus.FAILED,
                "all_providers_failed",
                "所有已启用文献来源均请求失败。",
            )
        if provider_failure:
            return (
                SearchRunStatus.PARTIAL_FAILED,
                "provider_partial_failed",
                "部分文献来源请求失败，结果仍可供审核。",
            )
        return SearchRunStatus.COMPLETED, None, None

    async def _publish(
        self,
        *,
        run: SearchRunRecord,
        snapshot: dict[str, Any],
        stage: SearchRunStage,
        message: str,
    ) -> None:
        session_key = run.redis_session_key
        if session_key is None:
            raise RuntimeError("候选相关性事件缺少 Redis 会话键。")
        await self._session_store.append_event(
            session_key,
            SearchProgressEvent(
                run_id=run.id,
                status=SearchRunStatus(snapshot.get("status", run.status)),
                stage=stage,
                provider_summary=snapshot.get("provider_summary", run.provider_summary),
                candidate_counts=snapshot.get("candidate_counts", {}),
                message=message,
            ).model_dump(mode="json"),
        )

    async def _renew_leases(
        self,
        *,
        session_key: str,
        lock_key: str,
        lock_token: str,
        arq_job_id: object,
    ) -> None:
        """持久续约运行锁、会话 TTL 与 ARQ in-progress 标记；它们都不是总超时。"""
        while True:
            renewed = await self._renew_leases_once(
                session_key=session_key,
                lock_key=lock_key,
                lock_token=lock_token,
                arq_job_id=arq_job_id,
            )
            if not renewed:
                logger.error(
                    "Candidate relevance lease ownership lost: run_id=%s", self._search_run_id
                )
                return
            await asyncio.sleep(_LEASE_HEARTBEAT_SECONDS)

    async def _renew_leases_once(
        self,
        *,
        session_key: str,
        lock_key: str,
        lock_token: str,
        arq_job_id: object,
    ) -> bool:
        """立即续约，避免长流首次等待时 ARQ 的默认占用标记先过期。"""
        renewed = await self._session_store.renew_lock(
            lock_key,
            token=lock_token,
            ttl_seconds=_LEASE_TTL_SECONDS,
        )
        if not renewed:
            return False
        await self._session_store.refresh_ttl(session_key)
        if isinstance(arq_job_id, str):
            await self._session_store.renew_arq_in_progress(
                arq_job_id,
                ttl_seconds=_LEASE_TTL_SECONDS,
            )
        return True
