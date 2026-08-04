"""多源检索 Worker 的确定性执行编排。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.settings import LiteratureSourceSettings, get_literature_source_settings
from app.db.models.workflow import ResearchPlan, SearchRun
from app.modules.search.citation_enrichment import CitationMetadataEnricher
from app.modules.search.contracts import (
    CandidateRelevanceLevel,
    CandidateRelevanceState,
    ProviderError,
    ProviderErrorCode,
    ProviderQuery,
    ProviderSearchResult,
    SourceName,
    UnifiedCandidate,
)
from app.modules.search.processing import process_provider_results
from app.modules.search.providers.base import SearchProvider
from app.modules.search.providers.doi_resolver import DoiMetadataResolver
from app.modules.search.providers.registry import ProviderRegistry, build_provider_registry
from app.modules.workflow.candidate_relevance import (
    OpenAICompatibleCandidateRelevanceEvaluator,
    mark_candidate_relevance_failed,
    mark_candidate_relevance_insufficient,
    skip_candidate_relevance,
)
from app.modules.workflow.contracts import ProviderSearchQuery, ResearchScope, SearchProgressEvent
from app.modules.workflow.job_queue import (
    ArqCandidateRelevanceJobQueue,
    CandidateRelevanceJobQueue,
    CandidateRelevanceQueueError,
)
from app.modules.workflow.query_plan import read_confirmed_query_plan
from app.modules.workflow.search_run_service import SearchRunService
from app.modules.workflow.search_session import SearchSessionStore
from app.modules.workflow.state import SearchRunStage, SearchRunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ProviderExecution:
    """一个来源的全部查询结果与来源级摘要。"""

    provider: SourceName
    results: tuple[ProviderSearchResult, ...]
    summary: dict[str, Any]


class SearchRunExecutor:
    """执行一次检索运行；所有外部来源均通过 Registry 注入。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        search_run: SearchRun,
        session_store: SearchSessionStore,
        literature_settings: LiteratureSourceSettings | None = None,
        registry: ProviderRegistry | None = None,
        citation_enricher: CitationMetadataEnricher | None = None,
        relevance_evaluator: OpenAICompatibleCandidateRelevanceEvaluator | None = None,
        relevance_queue: CandidateRelevanceJobQueue | None = None,
    ) -> None:
        self._session = session
        self._search_run = search_run
        self._session_store = session_store
        self._literature_settings = literature_settings or get_literature_source_settings()
        self._registry = registry or build_provider_registry(self._literature_settings)
        self._citation_enricher = citation_enricher
        self._relevance_evaluator = relevance_evaluator
        self._relevance_queue = relevance_queue or ArqCandidateRelevanceJobQueue()
        self._workflow_service = SearchRunService(session)

    async def execute(self) -> dict[str, str]:
        """执行查询计划并返回 Worker 可记录的最小结果。"""
        if self._search_run.redis_session_key is None:
            await self._workflow_service.complete_run(
                search_run_id=self._search_run.id,
                status=SearchRunStatus.FAILED,
                provider_summary={},
                candidate_counts={},
                error_code="search_session_key_missing",
                error_message="检索运行缺少 Redis 会话键。",
            )
            return self._result(SearchRunStatus.FAILED)

        plan = await self._load_plan()
        if plan is None:
            await self._fail(
                code="search_plan_missing",
                message="检索运行对应的研究计划不存在。",
            )
            return self._result(SearchRunStatus.FAILED)

        try:
            query_specs, scope = read_confirmed_query_plan(plan)
        except ValueError as exc:
            await self._fail(code="search_plan_data_invalid", message=str(exc))
            return self._result(SearchRunStatus.FAILED)

        provider_queries = self._group_provider_queries(query_specs, scope)
        if not provider_queries:
            await self._fail(
                code="no_enabled_provider",
                message="当前研究计划没有可执行的已启用文献来源。",
            )
            return self._result(SearchRunStatus.FAILED)

        provider_summary: dict[str, Any] = {
            provider.value: {
                "status": "queued",
                "query_count": len(queries),
                "result_count": 0,
                "raw_candidate_count": 0,
                "errors": [],
            }
            for provider, queries in provider_queries.items()
        }
        candidate_counts: dict[str, Any] = {}
        await self._publish(
            status=SearchRunStatus.RUNNING,
            stage=SearchRunStage.PROVIDER_SEARCH,
            provider_summary=provider_summary,
            candidate_counts=candidate_counts,
            candidates=(),
            message="正在调用已启用的文献来源。",
        )
        await self._workflow_service.update_progress(
            search_run_id=self._search_run.id,
            stage=SearchRunStage.PROVIDER_SEARCH,
            provider_summary=provider_summary,
            candidate_counts=candidate_counts,
        )

        executions = await self._execute_providers(provider_queries)
        provider_summary = {execution.provider.value: execution.summary for execution in executions}
        all_results = tuple(result for execution in executions for result in execution.results)
        successful_provider_count = sum(
            any(result.error is None for result in execution.results) for execution in executions
        )
        failed_provider_count = sum(
            any(result.error is not None for result in execution.results)
            for execution in executions
        )
        await self._publish(
            status=SearchRunStatus.RUNNING,
            stage=SearchRunStage.NORMALIZE,
            provider_summary=provider_summary,
            candidate_counts=candidate_counts,
            candidates=(),
            message="来源结果已返回，正在规整字段。",
        )

        processing_query = ProviderQuery(
            query=query_specs[0].query,
            limit=25,
            from_publication_year=scope.start_year,
            to_publication_year=scope.end_year,
        )
        processed = process_provider_results(all_results, processing_query)
        candidate_counts = {
            "raw_candidate_count": processed.raw_candidate_count,
            "deduplicated_candidate_count": processed.deduplicated_candidate_count,
            "included_candidate_count": processed.included_candidate_count,
            "candidate_count": len(processed.candidates),
            "excluded_candidate_count": len(processed.candidates)
            - processed.included_candidate_count,
        }
        await self._workflow_service.update_progress(
            search_run_id=self._search_run.id,
            stage=SearchRunStage.TRIAGE,
            provider_summary=provider_summary,
            candidate_counts=candidate_counts,
        )
        await self._publish(
            status=SearchRunStatus.RUNNING,
            stage=SearchRunStage.TRIAGE,
            provider_summary=provider_summary,
            candidate_counts=candidate_counts,
            candidates=processed.candidates,
            message="候选已完成规整、去重和基础筛选。",
        )

        candidates = await self._prepare_relevance(
            candidates=processed.candidates,
            provider_summary=provider_summary,
            candidate_counts=candidate_counts,
        )
        try:
            await self._relevance_queue.enqueue_relevance(
                search_run_id=self._search_run.id,
                attempt_id=uuid4().hex,
            )
        except CandidateRelevanceQueueError:
            unavailable_candidates = tuple(
                mark_candidate_relevance_failed(
                    candidate,
                    "候选相关性任务暂时无法启动，请稍后重新分析。",
                    code="candidate_relevance_queue_unavailable",
                )
                if candidate.relevance_state is CandidateRelevanceState.PENDING
                else candidate
                for candidate in candidates
            )
            candidate_counts.update(self._relevance_counts(unavailable_candidates))
            final_status = (
                SearchRunStatus.FAILED
                if successful_provider_count == 0
                else SearchRunStatus.PARTIAL_FAILED
            )
            message = "候选已展示，但相关性任务无法启动。"
            await self._workflow_service.complete_run(
                search_run_id=self._search_run.id,
                status=final_status,
                provider_summary=provider_summary,
                candidate_counts=candidate_counts,
                error_code="candidate_relevance_queue_unavailable",
                error_message=message,
            )
            await self._publish(
                status=final_status,
                stage=SearchRunStage.COMPLETED,
                provider_summary=provider_summary,
                candidate_counts=candidate_counts,
                candidates=unavailable_candidates,
                message=message,
            )
            return self._result(final_status)

        _ = failed_provider_count
        return {"search_run_id": str(self._search_run.id), "status": "relevance_queued"}

    async def _load_plan(self) -> ResearchPlan | None:
        """读取检索运行绑定的计划，Worker 不能自行寻找其他版本。"""
        return await self._session.scalar(
            select(ResearchPlan).where(ResearchPlan.id == self._search_run.research_plan_id)
        )

    def _group_provider_queries(
        self,
        query_specs: list[ProviderSearchQuery],
        scope: ResearchScope,
    ) -> dict[SourceName, list[ProviderQuery]]:
        """将确认后的来源查询映射为已启用 Provider 可执行的统一查询。"""
        grouped: dict[SourceName, list[ProviderQuery]] = defaultdict(list)
        for spec in query_specs:
            try:
                source = SourceName(spec.provider)
            except ValueError:
                continue
            if self._registry.get(source) is None:
                continue
            grouped[source].append(
                ProviderQuery(
                    query=spec.query,
                    limit=25,
                    from_publication_year=scope.start_year,
                    to_publication_year=scope.end_year,
                )
            )
        return dict(grouped)

    async def _execute_providers(
        self,
        provider_queries: dict[SourceName, list[ProviderQuery]],
    ) -> tuple[ProviderExecution, ...]:
        """按来源并发执行，单来源内部按查询顺序调用以复用其限速器。"""
        semaphore = asyncio.Semaphore(self._literature_settings.search_max_concurrent_providers)

        async def execute_one(
            source: SourceName,
            queries: list[ProviderQuery],
        ) -> ProviderExecution:
            provider = self._registry.get(source)
            assert provider is not None
            async with semaphore:
                results: list[ProviderSearchResult] = []
                for query in queries:
                    results.append(await self._call_provider(provider, query))
            errors = [result.error.model_dump(mode="json") for result in results if result.error]
            summary = {
                "status": "failed"
                if len(errors) == len(results)
                else "partial"
                if errors
                else "completed",
                "query_count": len(queries),
                "result_count": len(results),
                "raw_candidate_count": sum(len(result.candidates) for result in results),
                "errors": errors,
            }
            return ProviderExecution(source, tuple(results), summary)

        executions = await asyncio.gather(
            *(execute_one(source, queries) for source, queries in provider_queries.items())
        )
        return tuple(executions)

    @staticmethod
    async def _call_provider(
        provider: SearchProvider,
        query: ProviderQuery,
    ) -> ProviderSearchResult:
        """将未预期的 Provider 异常转换为来源级失败，而不阻断其他来源。"""
        try:
            return await provider.search(query)
        except Exception:
            return ProviderSearchResult(
                provider=provider.source,
                retrieved_at=datetime.now(UTC),
                error=ProviderError(
                    code=ProviderErrorCode.NETWORK_ERROR,
                    message="文献来源执行时发生未预期错误，请稍后重试。",
                    retryable=True,
                ),
            )

    async def _enrich_citations(
        self,
        candidates: tuple[UnifiedCandidate, ...],
    ) -> tuple[UnifiedCandidate, ...]:
        """仅对前 N 条已纳入候选执行 DOI 题录补全，控制来源请求规模。"""
        if (
            self._citation_enricher is None
            or self._literature_settings.search_citation_enrichment_limit == 0
        ):
            return candidates
        limit = self._literature_settings.search_citation_enrichment_limit
        citation_enricher = self._citation_enricher
        assert citation_enricher is not None
        included_ids = [
            candidate.candidate_id
            for candidate in candidates
            if candidate.triage is not None
            and candidate.triage.included
            and candidate.relevance_assessment is not None
            and candidate.relevance_assessment.level
            in {CandidateRelevanceLevel.CORE, CandidateRelevanceLevel.RELATED}
        ]
        selected_ids = set(included_ids[:limit])
        semaphore = asyncio.Semaphore(8)

        async def enrich(candidate: UnifiedCandidate) -> UnifiedCandidate:
            if candidate.candidate_id not in selected_ids:
                return candidate
            async with semaphore:
                return await citation_enricher.enrich(candidate)

        return tuple(await asyncio.gather(*(enrich(candidate) for candidate in candidates)))

    async def _prepare_relevance(
        self,
        *,
        candidates: tuple[UnifiedCandidate, ...],
        provider_summary: dict[str, Any],
        candidate_counts: dict[str, Any],
    ) -> tuple[UnifiedCandidate, ...]:
        """发布候选快照并标记待流式分析项，不在 Provider Worker 内等待模型。"""
        prepared_candidates = tuple(
            candidate.model_copy(
                update={
                    "relevance_state": CandidateRelevanceState.PENDING,
                    "relevance_assessment": None,
                    "relevance_error": None,
                }
            )
            if candidate.triage and candidate.triage.included and candidate.abstract
            else mark_candidate_relevance_insufficient(candidate)
            if candidate.triage and candidate.triage.included
            else skip_candidate_relevance(candidate)
            for candidate in candidates
        )
        candidate_counts.update(self._relevance_counts(prepared_candidates))
        await self._workflow_service.update_progress(
            search_run_id=self._search_run.id,
            stage=SearchRunStage.RELEVANCE_ASSESSMENT,
            provider_summary=provider_summary,
            candidate_counts=candidate_counts,
        )
        await self._publish(
            status=SearchRunStatus.RUNNING,
            stage=SearchRunStage.RELEVANCE_ASSESSMENT,
            provider_summary=provider_summary,
            candidate_counts=candidate_counts,
            candidates=prepared_candidates,
            message="候选已展示，正在依据标题和摘要分析相关性。",
        )
        return prepared_candidates

    @staticmethod
    def _relevance_counts(candidates: tuple[UnifiedCandidate, ...]) -> dict[str, int]:
        """统一计算相关性阶段统计，区分待流式分析、完成、信息不足和失败。"""
        return {
            "relevance_total_count": sum(
                candidate.triage is not None and candidate.triage.included
                for candidate in candidates
            ),
            "relevance_pending_count": sum(
                candidate.relevance_state is CandidateRelevanceState.PENDING
                for candidate in candidates
            ),
            "relevance_completed_count": sum(
                candidate.relevance_state is CandidateRelevanceState.COMPLETED
                for candidate in candidates
            ),
            "relevance_insufficient_count": sum(
                candidate.relevance_assessment is not None
                and candidate.relevance_assessment.level
                is CandidateRelevanceLevel.INSUFFICIENT_INFORMATION
                for candidate in candidates
            ),
            "relevance_failed_count": sum(
                candidate.relevance_state is CandidateRelevanceState.FAILED
                for candidate in candidates
            ),
        }

    async def _publish(
        self,
        *,
        status: SearchRunStatus,
        stage: SearchRunStage,
        provider_summary: dict[str, Any],
        candidate_counts: dict[str, Any],
        candidates: tuple[UnifiedCandidate, ...],
        message: str | None,
    ) -> None:
        """同时写入 Redis 快照与可恢复事件，保证 SSE 看到同一份状态。"""
        session_key = self._search_run.redis_session_key
        assert session_key is not None
        snapshot = {
            "run_id": str(self._search_run.id),
            "status": status.value,
            "stage": stage.value,
            "provider_summary": provider_summary,
            "candidate_counts": candidate_counts,
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        }
        await self._session_store.write_snapshot(session_key, snapshot)
        event = SearchProgressEvent(
            run_id=self._search_run.id,
            status=status,
            stage=stage,
            provider_summary=provider_summary,
            candidate_counts=candidate_counts,
            message=message,
        )
        await self._session_store.append_event(session_key, event.model_dump(mode="json"))

    async def _fail(self, *, code: str, message: str) -> None:
        """将执行前置错误写入数据库和 Redis，避免任务静默结束。"""
        await self._workflow_service.complete_run(
            search_run_id=self._search_run.id,
            status=SearchRunStatus.FAILED,
            provider_summary={},
            candidate_counts={},
            error_code=code,
            error_message=message,
        )
        await self._publish(
            status=SearchRunStatus.FAILED,
            stage=SearchRunStage.COMPLETED,
            provider_summary={},
            candidate_counts={},
            candidates=(),
            message=message,
        )

    def _result(self, status: SearchRunStatus) -> dict[str, str]:
        """返回不包含候选正文的 Worker 结果，避免 arq 日志膨胀。"""
        return {"search_run_id": str(self._search_run.id), "status": status.value}


def build_citation_enricher(settings: LiteratureSourceSettings) -> CitationMetadataEnricher:
    """根据文献来源配置创建 DOI 题录补全器。"""
    return CitationMetadataEnricher(DoiMetadataResolver(settings.doi_resolver))
