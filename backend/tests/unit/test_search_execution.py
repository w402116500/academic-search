"""检索执行器的来源并发和失败隔离测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from app.core.settings import LiteratureSourceSettings
from app.db.models.workflow import ResearchPlan, SearchRun
from app.modules.search.citation_enrichment import CitationMetadataEnricher
from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateLinks,
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
from app.modules.search.providers.registry import ProviderRegistry
from app.modules.workflow.search_execution import (
    SearchRunExecutor,
)
from app.modules.workflow.search_run_service import SearchRunService
from app.modules.workflow.search_session import SearchSessionStore
from sqlalchemy.ext.asyncio import AsyncSession


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


class FakeProvider:
    """可返回成功候选或来源级错误的 Provider 替身。"""

    def __init__(self, source: SourceName, result: ProviderSearchResult) -> None:
        self.source = source
        self._result = result

    async def search(self, query: ProviderQuery) -> ProviderSearchResult:
        _ = query
        return self._result


class CountingCitationEnricher:
    """只记录题录补全调用，验证执行器不会遗漏后半部分候选。"""

    def __init__(self) -> None:
        self.calls: list[UUID] = []

    async def enrich(self, candidate: UnifiedCandidate) -> UnifiedCandidate:
        """返回原候选，测试只关注执行器的选择范围而非 DOI 网络请求。"""
        self.calls.append(candidate.candidate_id)
        return candidate


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


def _run() -> SearchRun:
    """构造带 Redis 会话键的最小运行头。"""
    return SearchRun(
        id=UUID("00000000-0000-0000-0000-000000000501"),
        collection_id=UUID("00000000-0000-0000-0000-000000000502"),
        research_plan_id=UUID("00000000-0000-0000-0000-000000000503"),
        redis_session_key="academic-search:search-run:test",
        status="running",
        stage="provider_search",
        attempt_no=1,
        provider_summary={},
        candidate_counts={},
    )


def _plan() -> ResearchPlan:
    """构造足以建立相关性上下文的已确认计划。"""
    return ResearchPlan(
        id=UUID("00000000-0000-0000-0000-000000000504"),
        collection_id=UUID("00000000-0000-0000-0000-000000000502"),
        revision=1,
        raw_request="城市绿地如何影响老年人的心理健康？",
        status="confirmed",
        direction_options=[
            {
                "id": "green-space",
                "title": "城市绿地与心理健康",
                "summary": "评估绿地暴露和心理健康之间的关系。",
                "subtopics": ["绿地暴露", "心理健康"],
            }
        ],
        selected_direction_id="green-space",
        scope={},
        query_plan={},
        model_snapshot={},
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


def _included_candidate(index: int) -> UnifiedCandidate:
    """构造已通过初筛的候选，模拟最终结果页中的多条文献。"""
    doi = f"10.1000/example-{index}"
    source_record = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id=f"W-{index}",
        title=f"Evidence record {index}",
        authors=(CandidateAuthor(name="Ada Lovelace"),),
        doi=doi,
    )
    return UnifiedCandidate(
        doi=doi,
        title=source_record.title,
        title_key=f"evidence record {index}",
        authors=source_record.authors,
        links=CandidateLinks(landing_url=f"https://doi.org/{doi}"),
        source_records=(source_record,),
        triage=TriageDecision(included=True),
        relevance_state=CandidateRelevanceState.COMPLETED,
        relevance_assessment=CandidateRelevanceAssessment(
            level=CandidateRelevanceLevel.CORE,
            study_focus="用于测试题录预取范围。",
            reason="测试候选与研究方向相关。",
            helpful_aspect="用于测试题录预取范围。",
            recommendation="测试用。",
            evidence=(CandidateRelevanceEvidence(source_field="title", quote=source_record.title),),
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
    settings = LiteratureSourceSettings(
        search_max_concurrent_providers=2,
        search_citation_enrichment_limit=0,
    )
    executor = SearchRunExecutor(
        session=cast(AsyncSession, object()),
        search_run=_run(),
        session_store=cast(SearchSessionStore, FakeStore()),
        literature_settings=settings,
        registry=ProviderRegistry([openalex, crossref]),
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


@pytest.mark.asyncio
async def test_citation_enrichment_prefetches_only_high_relevance_candidates() -> None:
    """题录预取只处理高相关统一候选，避免阻塞低优先级候选展示。"""
    settings = LiteratureSourceSettings()
    enricher = CountingCitationEnricher()
    executor = SearchRunExecutor(
        session=cast(AsyncSession, object()),
        search_run=_run(),
        session_store=cast(SearchSessionStore, FakeStore()),
        literature_settings=settings,
        registry=ProviderRegistry([]),
        citation_enricher=cast(CitationMetadataEnricher, enricher),
    )
    candidates = tuple(_included_candidate(index) for index in range(50))

    enriched = await executor._enrich_citations(candidates)

    assert settings.search_citation_enrichment_limit == 12
    assert enriched == candidates
    assert len(enricher.calls) == 12


def test_session_store_keys_are_scoped_to_the_run() -> None:
    """执行器接收的会话键必须是服务端按运行 UUID 生成的键。"""
    assert _run().redis_session_key == "academic-search:search-run:test"


@pytest.mark.asyncio
async def test_search_executor_publishes_full_collection_as_pending_before_queueing() -> None:
    """Provider Worker 只准备完整候选集合，不在其中发起模型调用。"""
    store = FakeStore()
    executor = SearchRunExecutor(
        session=cast(AsyncSession, object()),
        search_run=_run(),
        session_store=cast(SearchSessionStore, store),
        literature_settings=LiteratureSourceSettings(),
        registry=ProviderRegistry([]),
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
    assert latest_counts["relevance_pending_count"] == 50


@pytest.mark.asyncio
async def test_search_executor_marks_abstractless_candidates_without_model_failure() -> None:
    """没有摘要的候选由确定性规则完成，不等待或依赖模型配置。"""
    executor = SearchRunExecutor(
        session=cast(AsyncSession, object()),
        search_run=_run(),
        session_store=cast(SearchSessionStore, FakeStore()),
        literature_settings=LiteratureSourceSettings(),
        registry=ProviderRegistry([]),
    )
    executor._workflow_service = cast(SearchRunService, FakeWorkflowService())

    prepared = await executor._prepare_relevance(
        candidates=(_eligible_candidate(1), _eligible_candidate(2, abstract=None)),
        provider_summary={},
        candidate_counts={"candidate_count": 2},
    )

    assert prepared[0].relevance_state is CandidateRelevanceState.PENDING
    assert prepared[1].relevance_state is CandidateRelevanceState.COMPLETED
    assert prepared[1].relevance_assessment is not None
    assert (
        prepared[1].relevance_assessment.level is CandidateRelevanceLevel.INSUFFICIENT_INFORMATION
    )
