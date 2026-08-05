"""多源检索 Worker 的确定性执行编排。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from app.modules.research.plan_contracts import ProviderSearchQuery, ResearchScope
from app.modules.research.plan_models import ResearchPlanRecord
from app.modules.research.query_plan import read_confirmed_query_plan
from app.modules.search.api_contracts import CandidateCounts, SearchProgressEvent
from app.modules.search.contracts import (
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
from app.modules.search.providers.registry import ProviderRegistry
from app.modules.search.queue import CandidateRelevanceJobQueue, CandidateRelevanceQueueError
from app.modules.search.relevance import (
    exclude_candidate_relevance,
    is_screening_candidate,
    mark_candidate_relevance_insufficient,
    skip_candidate_relevance,
)
from app.modules.search.run_models import SearchRunRecord
from app.modules.search.run_repository import SearchRunRepository
from app.modules.search.run_service import SearchRunService
from app.modules.search.session import SearchSessionStore
from app.modules.search.state import SearchRunStage, SearchRunStatus


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
        runs: SearchRunRepository,
        search_run: SearchRunRecord,
        session_store: SearchSessionStore,
        relevance_queue: CandidateRelevanceJobQueue,
        registry: ProviderRegistry,
        max_concurrent_providers: int,
    ) -> None:
        if max_concurrent_providers < 1:
            raise ValueError("文献来源最大并发数必须至少为 1。")
        self._runs = runs
        self._search_run = search_run
        self._session_store = session_store
        self._registry = registry
        self._max_concurrent_providers = max_concurrent_providers
        self._relevance_queue = relevance_queue
        self._workflow_service = SearchRunService(runs)

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
        for queue_attempt in (1, 2):
            try:
                await self._relevance_queue.enqueue_relevance(
                    search_run_id=self._search_run.id,
                    attempt_no=1,
                )
                break
            except CandidateRelevanceQueueError:
                if queue_attempt == 1:
                    continue
                unavailable_candidates = tuple(
                    exclude_candidate_relevance(
                        candidate,
                        "候选相关性任务暂时无法启动，当前候选不会进入筛选。",
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
                    if failed_provider_count
                    else SearchRunStatus.COMPLETED
                )
                error_code = "all_providers_failed" if successful_provider_count == 0 else None
                message = (
                    "所有已启用文献来源均请求失败。"
                    if successful_provider_count == 0
                    else "部分文献来源请求失败，结果仍可供审核。"
                    if failed_provider_count
                    else "检索完成，候选文献已准备好。"
                )
                await self._workflow_service.complete_run(
                    search_run_id=self._search_run.id,
                    status=final_status,
                    provider_summary=provider_summary,
                    candidate_counts=candidate_counts,
                    error_code=error_code,
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

        return {"search_run_id": str(self._search_run.id), "status": "relevance_queued"}

    async def _load_plan(self) -> ResearchPlanRecord | None:
        """读取检索运行绑定的计划，Worker 不能自行寻找其他版本。"""
        return await self._runs.get_plan(self._search_run.research_plan_id)

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
        semaphore = asyncio.Semaphore(self._max_concurrent_providers)

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
            relevance_attempt_no=1,
        )
        return prepared_candidates

    @staticmethod
    def _relevance_counts(candidates: tuple[UnifiedCandidate, ...]) -> dict[str, int]:
        """只发布用户所需的分析、排除与可筛选候选统计。"""
        return {
            "relevance_total_count": sum(
                candidate.triage is not None and candidate.triage.included
                for candidate in candidates
            ),
            "relevance_analyzed_count": sum(
                candidate.triage is not None
                and candidate.triage.included
                and candidate.relevance_state
                in {CandidateRelevanceState.COMPLETED, CandidateRelevanceState.EXCLUDED}
                for candidate in candidates
            ),
            "relevance_excluded_count": sum(
                candidate.triage is not None
                and candidate.triage.included
                and candidate.relevance_state is CandidateRelevanceState.EXCLUDED
                for candidate in candidates
            ),
            "screening_candidate_count": sum(
                is_screening_candidate(candidate) for candidate in candidates
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
        relevance_attempt_no: int | None = None,
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
        if relevance_attempt_no is not None:
            snapshot["relevance_attempt_no"] = relevance_attempt_no
        await self._session_store.write_snapshot(session_key, snapshot)
        event = SearchProgressEvent(
            run_id=self._search_run.id,
            status=status,
            stage=stage,
            provider_summary=provider_summary,
            candidate_counts=cast(CandidateCounts, candidate_counts),
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
