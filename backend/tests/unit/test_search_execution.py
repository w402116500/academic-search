"""检索执行器的来源并发和失败隔离测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from app.modules.search.contracts import (
    CandidateRelevanceAssessment,
    CandidateRelevanceEvidence,
    CandidateRelevanceLevel,
    CandidateRelevanceState,
    ProviderError,
    ProviderErrorCode,
    ProviderQuery,
    ProviderSearchResult,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.search.execution import (
    SearchRunExecutor,
)
from app.modules.search.providers.registry import ProviderRegistry
from app.modules.search.run_models import SearchRunRecord
from app.modules.search.run_repository import SearchRunRepository
from app.modules.search.run_service import SearchRunService
from app.modules.search.session import SearchSessionStore


class FakeStore:
    """不访问 Redis 的执行器测试替身。"""

    def __init__(self) -> None:
        self.snapshots: list[dict[str, object]] = []
        self.events: list[dict[str, object]] = []

    async def write_snapshot(self, _key: str, snapshot: dict[str, object]) -> None:
        self.snapshots.append(snapshot)

    async def append_event(self, _key: str, event: dict[str, object]) -> str:
        self.events.append(event)
        return "1-0"


class UnexpectedRelevanceQueue:
    """Constructor dependency for tests that do not dispatch relevance work."""

    async def enqueue_relevance(self, *, search_run_id: UUID, attempt_no: int) -> str:
        raise AssertionError(
            f"unexpected relevance dispatch: {search_run_id}, attempt {attempt_no}"
        )


class FakeProvider:
    """可返回成功候选或来源级错误的 Provider 替身。"""

    def __init__(self, source: SourceName, result: ProviderSearchResult) -> None:
        self.source = source
        self._result = result

    async def search(self, query: ProviderQuery) -> ProviderSearchResult:
        _ = query
        return self._result


class RecordingRelevanceEvaluator:
    """记录集合级调用边界，避免测试依赖外部聊天模型。"""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, ...]] = []

    async def assess(
        self, *, context: object, candidates: tuple[UnifiedCandidate, ...]
    ) -> tuple[UnifiedCandidate, ...]:
        _ = context
        self.calls.append(tuple(candidate.candidate_id for candidate in candidates))
        return tuple(
            candidate.model_copy(
                update={
                    "relevance_state": CandidateRelevanceState.COMPLETED,
                    "relevance_assessment": CandidateRelevanceAssessment(
                        level=CandidateRelevanceLevel.CORE,
                        study_focus="用于验证集合级调用。",
                        reason="测试候选与当前研究方向相关。",
                        helpful_aspect="用于验证集合级调用。",
                        recommendation="测试用。",
                        evidence=(
                            CandidateRelevanceEvidence(
                                source_field="title",
                                quote=candidate.title,
                            ),
                        ),
                    ),
                    "relevance_error": None,
                }
            )
            for candidate in candidates
        )


class FakeWorkflowService:
    """相关性阶段只需要记录进度，不访问数据库。"""

    async def update_progress(self, **_kwargs: object) -> None:
        return None


def _run() -> SearchRunRecord:
    """构造带 Redis 会话键的最小运行头。"""
    now = datetime.now(UTC)
    return SearchRunRecord(
        id=UUID("00000000-0000-0000-0000-000000000501"),
        collection_id=UUID("00000000-0000-0000-0000-000000000502"),
        research_plan_id=UUID("00000000-0000-0000-0000-000000000503"),
        arq_job_id=None,
        redis_session_key="academic-search:search-run:test",
        status="running",
        stage="provider_search",
        attempt_no=1,
        provider_summary={},
        candidate_counts={},
        error_code=None,
        error_message=None,
        started_at=now,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )


def _success(source: SourceName) -> ProviderSearchResult:
    """构造一个带最小标题的成功来源结果。"""
    return ProviderSearchResult(
        provider=source,
        retrieved_at=datetime.now(UTC),
        candidates=(
            RawCandidate(
                source=source,
                source_record_id=f"{source.value}-1",
                title="Urban green space and mental health",
            ),
        ),
    )


def _failure(source: SourceName) -> ProviderSearchResult:
    """构造一个不携带候选的来源失败结果。"""
    return ProviderSearchResult(
        provider=source,
        retrieved_at=datetime.now(UTC),
        error=ProviderError(
            code=ProviderErrorCode.TIMEOUT,
            message=f"{source.value} timeout",
            retryable=True,
        ),
    )


def _eligible_candidate(
    index: int, *, abstract: str | None = "Candidate abstract."
) -> UnifiedCandidate:
    """构造待集合级评估的候选，避免使用已完成评估的题录预取替身。"""
    source_record = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id=f"eligible-{index}",
        title=f"Eligible candidate {index}",
        abstract=abstract,
    )
    return UnifiedCandidate(
        title=source_record.title,
        title_key=source_record.title.casefold(),
        abstract=abstract,
        source_records=(source_record,),
        triage=TriageDecision(included=True),
    )


@pytest.mark.asyncio
async def test_execute_providers_isolates_one_source_failure() -> None:
    """一个来源超时时，其他来源仍返回成功结果并保留失败摘要。"""
    openalex = FakeProvider(SourceName.OPENALEX, _success(SourceName.OPENALEX))
    crossref = FakeProvider(SourceName.CROSSREF, _failure(SourceName.CROSSREF))
    executor = SearchRunExecutor(
        runs=cast(SearchRunRepository, object()),
        search_run=_run(),
        session_store=cast(SearchSessionStore, FakeStore()),
        relevance_queue=UnexpectedRelevanceQueue(),
        registry=ProviderRegistry([openalex, crossref]),
        max_concurrent_providers=2,
    )

    executions = await executor._execute_providers(
        {
            SourceName.OPENALEX: [ProviderQuery(query="green space")],
            SourceName.CROSSREF: [ProviderQuery(query="green space")],
        }
    )

    assert {execution.provider for execution in executions} == {
        SourceName.OPENALEX,
        SourceName.CROSSREF,
    }
    summaries = {execution.provider: execution.summary for execution in executions}
    assert summaries[SourceName.OPENALEX]["status"] == "completed"
    assert summaries[SourceName.CROSSREF]["status"] == "failed"
    assert summaries[SourceName.CROSSREF]["errors"][0]["code"] == "timeout"


def test_session_store_keys_are_scoped_to_the_run() -> None:
    """执行器接收的会话键必须是服务端按运行 UUID 生成的键。"""
    assert _run().redis_session_key == "academic-search:search-run:test"


@pytest.mark.asyncio
async def test_search_executor_publishes_full_collection_as_pending_before_queueing() -> None:
    """Provider Worker 只准备完整候选集合，不在其中发起模型调用。"""
    store = FakeStore()
    executor = SearchRunExecutor(
        runs=cast(SearchRunRepository, object()),
        search_run=_run(),
        session_store=cast(SearchSessionStore, store),
        relevance_queue=UnexpectedRelevanceQueue(),
        registry=ProviderRegistry([]),
        max_concurrent_providers=1,
    )
    executor._workflow_service = cast(SearchRunService, FakeWorkflowService())
    candidates = tuple(_eligible_candidate(index) for index in range(50))

    prepared = await executor._prepare_relevance(
        candidates=candidates,
        provider_summary={},
        candidate_counts={"candidate_count": len(candidates)},
    )

    assert len(prepared) == 50
    assert all(
        candidate.relevance_state is CandidateRelevanceState.PENDING for candidate in prepared
    )
    assert store.events[-1]["message"] == "候选已展示，正在依据标题和摘要分析相关性。"
    latest_counts = cast(dict[str, object], store.snapshots[-1]["candidate_counts"])
    assert latest_counts["relevance_total_count"] == 50
    assert latest_counts["relevance_analyzed_count"] == 0
    assert latest_counts["relevance_excluded_count"] == 0


@pytest.mark.asyncio
async def test_search_executor_marks_abstractless_candidates_without_model_failure() -> None:
    """没有摘要的候选由确定性规则完成，不等待或依赖模型配置。"""
    executor = SearchRunExecutor(
        runs=cast(SearchRunRepository, object()),
        search_run=_run(),
        session_store=cast(SearchSessionStore, FakeStore()),
        relevance_queue=UnexpectedRelevanceQueue(),
        registry=ProviderRegistry([]),
        max_concurrent_providers=1,
    )
    executor._workflow_service = cast(SearchRunService, FakeWorkflowService())

    prepared = await executor._prepare_relevance(
        candidates=(_eligible_candidate(1), _eligible_candidate(2, abstract=None)),
        provider_summary={},
        candidate_counts={"candidate_count": 2},
    )

    assert prepared[0].relevance_state is CandidateRelevanceState.PENDING
    assert prepared[1].relevance_state is CandidateRelevanceState.EXCLUDED
    assert prepared[1].relevance_assessment is not None
    assert (
        prepared[1].relevance_assessment.level is CandidateRelevanceLevel.INSUFFICIENT_INFORMATION
    )
