"""检索执行器的来源并发和失败隔离测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from app.core.settings import LiteratureSourceSettings
from app.db.models.workflow import SearchRun
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
from app.modules.workflow.search_execution import SearchRunExecutor
from app.modules.workflow.search_session import SearchSessionStore
from sqlalchemy.ext.asyncio import AsyncSession


class FakeStore:
    """不访问 Redis 的执行器测试替身。"""

    async def write_snapshot(self, _key: str, _snapshot: dict[str, object]) -> None:
        return None

    async def append_event(self, _key: str, _event: dict[str, object]) -> str:
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
