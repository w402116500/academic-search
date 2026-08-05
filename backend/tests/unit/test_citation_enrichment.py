"""题录补全合并规则的纯离线测试。"""

from __future__ import annotations

import pytest
from app.modules.literature.contracts import (
    CitationAuthor,
    CitationDate,
    CitationMetadataStatus,
    CitationResolutionError,
    CitationResolutionErrorCode,
    DoiCslRecord,
    DoiMetadataResolution,
)
from app.modules.search.citation_enrichment import CitationMetadataEnricher
from app.modules.search.contracts import (
    CandidateAuthor,
    RawCandidate,
    SourceName,
    UnifiedCandidate,
)
from app.modules.search.deduplicate import deduplicate_candidates
from app.modules.search.normalize import normalize_raw_candidate


class StubResolver:
    """记录调用次数的确定性 DOI 解析器，避免合并测试依赖网络。"""

    def __init__(self, result: DoiMetadataResolution) -> None:
        """保存预设解析结果；每次调用都返回同一结果。"""
        self._result = result
        self.calls: list[str] = []

    async def resolve(self, doi: str) -> DoiMetadataResolution:
        """记录 DOI，便于断言无 DOI 候选不会发起网络请求。"""
        self.calls.append(doi)
        return self._result


def _candidate(*, doi: str | None = "10.1000/crossref.example") -> UnifiedCandidate:
    """构建一个尚未取得卷期页码的统一期刊候选。"""
    raw_candidate = RawCandidate(
        source=SourceName.CROSSREF,
        source_record_id=doi or "arxiv-1234",
        title="Evidence for AI-supported academic writing",
        authors=(CandidateAuthor(name="Ada Lovelace"),),
        published_year=2024,
        doi=doi,
        venue="Journal of Research Methods",
        document_type="journal-article",
        landing_url="https://doi.org/10.1000/crossref.example" if doi else None,
    )
    return deduplicate_candidates((normalize_raw_candidate(raw_candidate),))[0]


def _record(*, title: str = "Evidence for AI-supported academic writing") -> DoiCslRecord:
    """构建包含完整正式题录字段的 DOI CSL 记录。"""
    return DoiCslRecord(
        source_url="https://doi.org/10.1000/crossref.example",
        doi="10.1000/crossref.example",
        authors=(CitationAuthor(given="Ada", family="Lovelace"),),
        title=title,
        document_type="article-journal",
        issued_date=CitationDate(year=2024, month=5, day=1),
        venue="Journal of Research Methods",
        volume="12",
        issue="3",
        pages="101-115",
        article_number="e102274",
        publisher="Academic Press",
        url="https://doi.org/10.1000/crossref.example",
    )


@pytest.mark.asyncio
async def test_enrichment_fills_missing_fields_and_preserves_structured_doi_authors() -> None:
    """同值字段可由 DOI 提升精度，缺失卷期页码必须标记其权威来源。"""
    resolver = StubResolver(DoiMetadataResolution(doi="10.1000/crossref.example", record=_record()))
    enriched = await CitationMetadataEnricher(resolver).enrich(_candidate())

    assert resolver.calls == ["10.1000/crossref.example"]
    assert enriched.citation is not None
    assert enriched.citation.status is CitationMetadataStatus.READY
    assert enriched.citation.authors[0].given == "Ada"
    assert enriched.citation.authors[0].family == "Lovelace"
    assert enriched.citation.issued_date is not None
    assert enriched.citation.issued_date.to_csl_date_parts() == [2024, 5, 1]
    assert enriched.citation.volume == "12"
    assert enriched.citation.pages == "101-115"
    assert enriched.citation.field_provenance["volume"] == "doi_content_negotiation"
    assert not enriched.citation.conflicts


@pytest.mark.asyncio
async def test_enrichment_keeps_candidate_value_and_marks_conflicting_doi_title() -> None:
    """正式来源与当前候选标题不一致时不能静默选择任意一方。"""
    resolver = StubResolver(
        DoiMetadataResolution(
            doi="10.1000/crossref.example",
            record=_record(title="A different title from the DOI registry"),
        )
    )
    enriched = await CitationMetadataEnricher(resolver).enrich(_candidate())

    assert enriched.citation is not None
    assert enriched.citation.status is CitationMetadataStatus.CONFLICT
    assert enriched.citation.title == "Evidence for AI-supported academic writing"
    assert enriched.citation.conflicts["title"] == (
        "Evidence for AI-supported academic writing",
        "A different title from the DOI registry",
    )


@pytest.mark.asyncio
async def test_enrichment_uses_doi_record_for_non_identity_metadata_differences() -> None:
    """作者写法、日期版本、刊物和 DOI URL 差异不应阻断正式题录。"""
    candidate = _candidate().model_copy(
        update={
            "authors": (CandidateAuthor(name="Barbara Händel"),),
            "published_date": CitationDate(year=2016, month=8, day=9),
            "venue": "Institutional Repository",
        }
    )
    record = _record().model_copy(
        update={
            "authors": (CitationAuthor(given="Barbara Friederike", family="Händel"),),
            "issued_date": CitationDate(year=2016, month=10),
            "venue": "Journal of Research Methods",
            "url": "http://dx.doi.org/10.1000/crossref.example",
        }
    )
    resolver = StubResolver(
        DoiMetadataResolution(
            doi="10.1000/crossref.example",
            record=record,
        )
    )

    enriched = await CitationMetadataEnricher(resolver).enrich(candidate)

    assert enriched.citation is not None
    assert enriched.citation.status is CitationMetadataStatus.READY
    assert enriched.citation.authors[0].given == "Barbara Friederike"
    assert enriched.citation.issued_date is not None
    assert enriched.citation.issued_date.to_csl_date_parts() == [2016, 10]
    assert enriched.citation.venue == "Journal of Research Methods"
    assert enriched.citation.url == "https://doi.org/10.1000/crossref.example"
    assert not enriched.citation.conflicts


@pytest.mark.asyncio
async def test_enrichment_without_doi_never_calls_resolver_and_reports_partial_metadata() -> None:
    """无 DOI 候选仍可继续展示，但不会触发无法执行的内容协商请求。"""
    unused_result = DoiMetadataResolution(doi="10.1000/unused", record=_record())
    resolver = StubResolver(unused_result)
    enriched = await CitationMetadataEnricher(resolver).enrich(_candidate(doi=None))

    assert resolver.calls == []
    assert enriched.citation is not None
    assert enriched.citation.status is CitationMetadataStatus.PARTIAL
    assert "doi" in enriched.citation.missing_fields


@pytest.mark.asyncio
async def test_enrichment_keeps_network_failure_distinct_from_missing_metadata() -> None:
    """DOI 服务不可用时应为 UNRESOLVED，以便前端提供重试而不是补录提示。"""
    resolver = StubResolver(
        DoiMetadataResolution(
            doi="10.1000/crossref.example",
            error=CitationResolutionError(
                code=CitationResolutionErrorCode.TIMEOUT,
                message="DOI 内容协商请求超时，请稍后重试。",
                retryable=True,
            ),
        )
    )
    enriched = await CitationMetadataEnricher(resolver).enrich(_candidate())

    assert enriched.citation is not None
    assert enriched.citation.status is CitationMetadataStatus.UNRESOLVED
    assert enriched.citation.resolution_error is not None
    assert enriched.citation.resolution_error.retryable is True
