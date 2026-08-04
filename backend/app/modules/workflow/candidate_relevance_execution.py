"""独立 Worker 执行完整候选集合的流式相关性分析。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any
from uuid import UUID, uuid4

from app.core.settings import LiteratureSourceSettings, get_literature_source_settings
from app.db.models.workflow import ResearchPlan, SearchRun
from app.modules.search.citation_enrichment import CitationMetadataEnricher
from app.modules.search.contracts import (
    CandidateRelevanceState,
    UnifiedCandidate,
)
from app.modules.search.providers.doi_resolver import DoiMetadataResolver
from app.modules.workflow.candidate_relevance import (
    CandidateRelevanceCancelled,
    OpenAICompatibleCandidateRelevanceEvaluator,
    build_candidate_relevance_context,
    mark_candidate_relevance_failed,
)
from app.modules.workflow.candidate_relevance_service import CandidateRelevanceService
from app.modules.workflow.contracts import SearchProgressEvent
from app.modules.workflow.query_plan import read_confirmed_query_plan
from app.modules.workflow.search_run_service import SearchRunService
from app.modules.workflow.search_session import (
    SearchSessionStore,
    build_candidate_relevance_lock_key,
)
from app.modules.workflow.settings import get_workflow_settings
from app.modules.workflow.state import SearchRunStage, SearchRunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_LEASE_TTL_SECONDS = 90
_LEASE_HEARTBEAT_SECONDS = 30


class CandidateRelevanceRunExecutor:
    """以完整集合流式调用模型，并通过 Redis 合并保护其他候选更新。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        search_run_id: UUID,
        session_store: SearchSessionStore,
        literature_settings: LiteratureSourceSettings | None = None,
    ) -> None:
        self._session = session
        self._search_run_id = search_run_id
        self._session_store = session_store
        self._literature_settings = literature_settings or get_literature_source_settings()
        self._workflow_service = SearchRunService(session)

    async def execute(self, *, arq_context: dict[str, Any]) -> dict[str, str]:
        """执行或安全忽略过期、重复或已取消的 relevance 队列消息。"""
        run = await self._session.scalar(
            select(SearchRun).where(SearchRun.id == self._search_run_id)
        )
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

    async def _execute_locked(self, run: SearchRun, session_key: str) -> dict[str, str]:
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
        if await self._is_cancelled(session_key):
            return {"search_run_id": str(run.id), "status": "cancelled"}

        candidates = CandidateRelevanceService._deserialize_candidates(snapshot)
        included = tuple(
            candidate
            for candidate in candidates
            if candidate.triage is not None and candidate.triage.included
        )
        try:
            assessed = await self._assess_collection(run, included, session_key)
        except CandidateRelevanceCancelled:
            return {"search_run_id": str(run.id), "status": "cancelled"}

        if await self._is_cancelled(session_key):
            return {"search_run_id": str(run.id), "status": "cancelled"}
        merged = await self._session_store.merge_snapshot(
            session_key,
            lambda current: self._merge_relevance(current, assessed),
        )
        merged = await self._session_store.merge_snapshot(
            session_key,
            lambda current: self._set_snapshot_stage(current, SearchRunStage.CITATION_ENRICHMENT),
        )
        merged_candidates = CandidateRelevanceService._deserialize_candidates(merged)
        merged_counts = CandidateRelevanceService._candidate_counts(merged, merged_candidates)
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
        final_candidates = CandidateRelevanceService._deserialize_candidates(final_snapshot)
        final_counts = CandidateRelevanceService._candidate_counts(final_snapshot, final_candidates)
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
        run: SearchRun,
        candidates: tuple[UnifiedCandidate, ...],
        session_key: str,
    ) -> tuple[UnifiedCandidate, ...]:
        """一次输入完整已纳入集合；模型和配置错误被映射为可重试候选失败。"""
        if not candidates:
            return ()
        plan = await self._session.scalar(
            select(ResearchPlan).where(ResearchPlan.id == run.research_plan_id)
        )
        if plan is None:
            return self._failed_candidates(
                candidates,
                "检索计划已不存在，无法重新生成候选理由。",
                "candidate_relevance_plan_missing",
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
            evaluator = OpenAICompatibleCandidateRelevanceEvaluator(get_workflow_settings())
            return await evaluator.assess(
                context=context,
                candidates=candidates,
                cancellation_check=lambda: self._is_cancelled(session_key),
            )
        except CandidateRelevanceCancelled:
            raise
        except Exception:
            logger.exception(
                "Candidate relevance execution failed before a complete validated response: "
                "run_id=%s candidate_count=%s",
                run.id,
                len(candidates),
            )
            return self._failed_candidates(
                candidates,
                "候选相关性模型暂时不可用，请稍后重新分析。",
                "candidate_relevance_model_unavailable",
            )

    @staticmethod
    def _failed_candidates(
        candidates: tuple[UnifiedCandidate, ...],
        message: str,
        code: str,
    ) -> tuple[UnifiedCandidate, ...]:
        return tuple(
            mark_candidate_relevance_failed(candidate, message, code=code)
            if candidate.abstract
            else candidate
            for candidate in candidates
        )

    @staticmethod
    def _merge_relevance(
        snapshot: dict[str, Any],
        assessed: tuple[UnifiedCandidate, ...],
    ) -> dict[str, Any]:
        """仅覆盖 relevance 字段，保留并发写入的题录、全文和选择状态。"""
        candidates = CandidateRelevanceService._deserialize_candidates(snapshot)
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
        merged["candidate_counts"] = CandidateRelevanceService._candidate_counts(
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
        candidates = CandidateRelevanceService._deserialize_candidates(snapshot)
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
        limit = self._literature_settings.search_citation_enrichment_limit
        if limit == 0:
            return candidates
        included_ids = [
            candidate.candidate_id
            for candidate in candidates
            if candidate.triage is not None
            and candidate.triage.included
            and candidate.relevance_assessment is not None
            and candidate.relevance_state is CandidateRelevanceState.COMPLETED
            and candidate.relevance_assessment.level.value in {"core", "related"}
        ]
        selected_ids = set(included_ids[:limit])
        enricher = CitationMetadataEnricher(
            DoiMetadataResolver(self._literature_settings.doi_resolver)
        )
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
        relevance_failure = int(candidate_counts.get("relevance_failed_count", 0)) > 0
        if not provider_success:
            return (
                SearchRunStatus.FAILED,
                "all_providers_failed",
                "所有已启用文献来源均请求失败。",
            )
        if provider_failure and relevance_failure:
            return (
                SearchRunStatus.PARTIAL_FAILED,
                "provider_and_candidate_relevance_partial_failed",
                "部分文献来源或候选相关性分析失败，结果仍可供审核和重新分析。",
            )
        if provider_failure:
            return (
                SearchRunStatus.PARTIAL_FAILED,
                "provider_partial_failed",
                "部分文献来源请求失败，结果仍可供审核。",
            )
        if relevance_failure:
            return (
                SearchRunStatus.PARTIAL_FAILED,
                "candidate_relevance_partial_failed",
                "部分候选相关性分析未完成，可基于当前完整候选集合重新分析。",
            )
        return SearchRunStatus.COMPLETED, None, None

    async def _publish(
        self,
        *,
        run: SearchRun,
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

    async def _is_cancelled(self, session_key: str) -> bool:
        return await self._session_store.is_relevance_cancellation_requested(session_key)

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
