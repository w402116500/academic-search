"""候选规范化、保守去重、字段合并和规则初筛测试。"""

from datetime import UTC, datetime

from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateLanguage,
    CitationDate,
    ProviderError,
    ProviderErrorCode,
    ProviderQuery,
    ProviderSearchResult,
    RawCandidate,
    SourceName,
    TriageReasonCode,
)
from app.modules.search.deduplicate import deduplicate_candidates
from app.modules.search.normalize import normalize_doi, normalize_raw_candidate, normalize_title_key
from app.modules.search.processing import process_provider_results


def make_candidate(
    source: SourceName,
    source_record_id: str,
    title: str,
    *,
    language: CandidateLanguage | None = None,
    doi: str | None = None,
    author_name: str = "Ada Lovelace",
    abstract: str | None = "A concise abstract.",
    published_year: int | None = 2023,
    published_date: CitationDate | None = None,
    venue: str | None = "Research Journal",
    document_type: str | None = "article",
    volume: str | None = None,
    issue: str | None = None,
    pages: str | None = None,
    article_number: str | None = None,
    publisher: str | None = None,
    citation_count: int | None = None,
    fulltext_url: str | None = None,
    is_open_access: bool | None = None,
) -> RawCandidate:
    """构造最小来源候选，便于让每个测试只突出一个匹配或筛选条件。"""
    return RawCandidate(
        source=source,
        source_record_id=source_record_id,
        source_record_url=f"https://source.example.org/{source_record_id}",
        title=title,
        language=language,
        authors=(CandidateAuthor(name=author_name),) if author_name else (),
        abstract=abstract,
        published_year=published_year,
        published_date=published_date,
        doi=doi,
        venue=venue,
        document_type=document_type,
        volume=volume,
        issue=issue,
        pages=pages,
        article_number=article_number,
        publisher=publisher,
        citation_count=citation_count,
        landing_url=f"https://landing.example.org/{source_record_id}",
        fulltext_url=fulltext_url,
        is_open_access=is_open_access,
    )


def make_provider_result(
    provider: SourceName,
    *candidates: RawCandidate,
    error: ProviderError | None = None,
) -> ProviderSearchResult:
    """构造一次来源调用结果；失败来源不携带候选，符合 Provider 契约。"""
    return ProviderSearchResult(
        provider=provider,
        candidates=candidates,
        retrieved_at=datetime.now(UTC),
        error=error,
    )


def test_normalization_removes_doi_transport_format_and_title_punctuation() -> None:
    """DOI URL 与标题标点差异不应影响跨来源匹配。"""
    assert normalize_doi(" DOI: https://doi.org/10.1000/Example.DOI. ") == "10.1000/example.doi"
    assert normalize_title_key("Large-Language Models: Writing!") == "large language models writing"


def test_normalize_raw_candidate_cleans_source_format_noise() -> None:
    """来源候选应统一解码 HTML、清理空白、规整 DOI 与文献类型。"""
    candidate = make_candidate(
        SourceName.SEMANTIC_SCHOLAR,
        "  source-id  ",
        "  \uff2c\uff2c\uff2d &amp;   Academic Writing  ",
        doi=" DOI: https://doi.org/10.1000/Example.DOI. ",
        author_name=" Ada&nbsp;Lovelace ",
        abstract=" A&nbsp;concise\nabstract. ",
        venue=" Journal&nbsp;of AI &amp; Education ",
        document_type="JournalArticle",
        fulltext_url=" https://example.org/article.pdf ",
    )

    normalized = normalize_raw_candidate(candidate)

    assert normalized.source_record_id == "source-id"
    assert normalized.title == "LLM & Academic Writing"
    assert normalized.authors[0].name == "Ada Lovelace"
    assert normalized.abstract == "A concise abstract."
    assert normalized.doi == "10.1000/example.doi"
    assert normalized.venue == "Journal of AI & Education"
    assert normalized.document_type == "journal_article"
    assert normalized.fulltext_url == "https://example.org/article.pdf"


def test_processing_merges_three_source_records_and_preserves_provenance() -> None:
    """同一论文的正式元数据和预印本记录应合并，但冲突与来源引用量不能丢失。"""
    openalex = make_candidate(
        SourceName.OPENALEX,
        "W1",
        "Large Language Models for Academic Writing",
        language=CandidateLanguage.ENGLISH,
        doi="https://doi.org/10.1000/Example.DOI.",
        abstract="An extended abstract from OpenAlex with methodological details.",
        published_year=2023,
        citation_count=12,
    )
    crossref = make_candidate(
        SourceName.CROSSREF,
        "10.1000/example.doi",
        "Large language models for academic writing",
        doi="10.1000/example.doi",
        abstract="A short Crossref abstract.",
        published_year=2024,
        published_date=CitationDate(year=2024, month=5, day=1),
        document_type="journal-article",
        volume="12",
        issue="3",
        pages="101-115",
        article_number="e102274",
        publisher="Academic Press",
        citation_count=9,
    )
    arxiv = make_candidate(
        SourceName.ARXIV,
        "2401.00001v1",
        "Large Language Models for Academic Writing",
        doi=None,
        abstract="An arXiv abstract.",
        published_year=2023,
        document_type="preprint",
        fulltext_url="https://arxiv.org/pdf/2401.00001v1",
        is_open_access=True,
    )
    semantic_error = ProviderError(
        code=ProviderErrorCode.REMOTE_ERROR,
        message="Semantic Scholar 返回 HTTP 429。",
        retryable=True,
        http_status_code=429,
    )

    result = process_provider_results(
        (
            make_provider_result(SourceName.OPENALEX, openalex),
            make_provider_result(SourceName.CROSSREF, crossref),
            make_provider_result(SourceName.ARXIV, arxiv),
            make_provider_result(SourceName.SEMANTIC_SCHOLAR, error=semantic_error),
        ),
        ProviderQuery(
            query="academic writing", from_publication_year=2020, to_publication_year=2025
        ),
    )

    assert result.raw_candidate_count == 3
    assert result.deduplicated_candidate_count == 1
    assert result.included_candidate_count == 1
    assert result.provider_errors[SourceName.SEMANTIC_SCHOLAR] == semantic_error

    candidate = result.candidates[0]
    assert candidate.doi == "10.1000/example.doi"
    assert candidate.title == "Large language models for academic writing"
    assert candidate.language is CandidateLanguage.ENGLISH
    assert candidate.abstract == "An extended abstract from OpenAlex with methodological details."
    assert candidate.citation_counts_by_source == {"openalex": 12, "crossref": 9}
    assert candidate.published_date == CitationDate(year=2024, month=5, day=1)
    assert candidate.volume == "12"
    assert candidate.issue == "3"
    assert candidate.pages == "101-115"
    assert candidate.article_number == "e102274"
    assert candidate.publisher == "Academic Press"
    assert candidate.links.fulltext_url == "https://arxiv.org/pdf/2401.00001v1"
    assert candidate.is_open_access is True
    assert len(candidate.source_records) == 3
    assert candidate.field_provenance["title"] is SourceName.CROSSREF
    assert candidate.field_provenance["language"] is SourceName.OPENALEX
    assert candidate.field_provenance["abstract"] is SourceName.OPENALEX
    assert "doi" not in candidate.conflicts
    assert "title" not in candidate.conflicts
    assert "authors" not in candidate.conflicts
    assert candidate.conflicts["published_year"] == ("2023", "2024")
    assert candidate.triage is not None
    assert candidate.triage.included is True
    assert TriageReasonCode.PREPRINT_ONLY not in candidate.triage.warnings
    assert TriageReasonCode.METADATA_CONFLICT in candidate.triage.warnings


def test_processing_infers_chinese_language_when_sources_omit_language() -> None:
    """来源未提供语言时，中文标题应以可解释规则标为中文候选。"""
    candidate = make_candidate(
        SourceName.CROSSREF,
        "10.1000/chinese-language",
        "城市绿地可达性与老年人心理健康",
        doi="10.1000/chinese-language",
    )

    result = process_provider_results(
        (make_provider_result(SourceName.CROSSREF, candidate),),
        ProviderQuery(query="城市绿地"),
    )

    assert result.candidates[0].language is CandidateLanguage.CHINESE
    assert "language" not in result.candidates[0].field_provenance


def test_similar_title_with_different_first_author_is_not_merged() -> None:
    """无 DOI 候选的标题即使完全一致，首位作者冲突时也不能自动合并。"""
    first = make_candidate(
        SourceName.OPENALEX,
        "W-first",
        "Methods for Academic Writing Research",
        doi=None,
        author_name="Ada Lovelace",
    )
    second = make_candidate(
        SourceName.ARXIV,
        "arxiv-second",
        "Methods for Academic Writing Research",
        doi=None,
        author_name="Grace Hopper",
    )

    candidates = deduplicate_candidates((first, second))

    assert len(candidates) == 2


def test_preprint_only_candidate_is_retained_with_explicit_warnings() -> None:
    """arXiv 独有候选可以继续参与排序，但必须提醒用户它尚无正式题录支撑。"""
    arxiv = make_candidate(
        SourceName.ARXIV,
        "2401.00002v1",
        "A New Preprint",
        doi=None,
        abstract=None,
        document_type="preprint",
        is_open_access=True,
    )

    result = process_provider_results(
        (make_provider_result(SourceName.ARXIV, arxiv),),
        ProviderQuery(query="new preprint"),
    )

    decision = result.candidates[0].triage
    assert decision is not None
    assert decision.included is True
    assert set(decision.warnings) == {
        TriageReasonCode.PREPRINT_ONLY,
        TriageReasonCode.MISSING_ABSTRACT,
        TriageReasonCode.MISSING_DOI,
    }


def test_dataset_candidate_is_excluded_by_deterministic_triage() -> None:
    """数据集等明显非论文内容不应进入后续昂贵的摘要评估阶段。"""
    dataset = make_candidate(
        SourceName.OPENALEX,
        "W-dataset",
        "Academic Writing Dataset",
        document_type="dataset",
    )

    result = process_provider_results(
        (make_provider_result(SourceName.OPENALEX, dataset),),
        ProviderQuery(query="academic writing"),
    )

    decision = result.candidates[0].triage
    assert decision is not None
    assert decision.included is False
    assert decision.exclusion_reasons == (TriageReasonCode.UNSUPPORTED_DOCUMENT_TYPE,)
