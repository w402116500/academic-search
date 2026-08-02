"""跨来源候选文献的保守去重与字段合并。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateLanguage,
    CandidateLinks,
    CitationDate,
    RawCandidate,
    SourceName,
    UnifiedCandidate,
)
from app.modules.search.normalize import (
    NormalizedCandidateRecord,
    infer_candidate_language,
    normalize_author_key,
    normalize_candidate_record,
    normalize_doi,
    normalize_title_key,
)

# 正式元数据来源优先承担展示字段；预印本仍会作为独立来源记录保留下来。
_FORMAL_SOURCE_PRIORITY = (
    SourceName.CROSSREF,
    SourceName.OPENALEX,
    SourceName.SEMANTIC_SCHOLAR,
    SourceName.ARXIV,
)
_HIGH_CONFIDENCE_TITLE_SIMILARITY = 96.0


@dataclass(slots=True)
class _CandidateCluster:
    """尚未导出为 UnifiedCandidate 的内部匹配分组。"""

    records: list[NormalizedCandidateRecord] = field(default_factory=list)

    def doi_keys(self) -> set[str]:
        """取得组内全部 DOI 键；不同 DOI 同时出现时由冲突字段显式展示。"""
        return {record.doi_key for record in self.records if record.doi_key is not None}


def deduplicate_candidates(candidates: Iterable[RawCandidate]) -> list[UnifiedCandidate]:
    """按 DOI 优先、元数据兜底的规则合并候选，并输出可展示的统一候选。"""
    clusters: list[_CandidateCluster] = []

    for candidate in candidates:
        record = normalize_candidate_record(candidate)
        matched_clusters = [cluster for cluster in clusters if _cluster_matches(cluster, record)]

        # 只有唯一匹配才自动合并；多组都相似时宁可保留新候选，也不制造错误关联。
        if len(matched_clusters) == 1:
            matched_clusters[0].records.append(record)
        else:
            clusters.append(_CandidateCluster(records=[record]))

    return [_build_unified_candidate(cluster.records) for cluster in clusters]


def _cluster_matches(cluster: _CandidateCluster, record: NormalizedCandidateRecord) -> bool:
    """判断新记录是否可安全加入现有组，避免不同 DOI 的论文被标题相似度误合并。"""
    cluster_doi_keys = cluster.doi_keys()

    if record.doi_key is not None and cluster_doi_keys:
        return record.doi_key in cluster_doi_keys

    return any(_metadata_matches(existing, record) for existing in cluster.records)


def _metadata_matches(
    first: NormalizedCandidateRecord,
    second: NormalizedCandidateRecord,
) -> bool:
    """使用标题、首位作者和年份做无 DOI 文献的高置信度匹配。"""
    if not _years_are_compatible(first.raw.published_year, second.raw.published_year):
        return False

    if not _first_authors_are_compatible(first.first_author_key, second.first_author_key):
        return False

    if first.title_key == second.title_key:
        return True

    return _title_similarity(first.title_key, second.title_key) >= _HIGH_CONFIDENCE_TITLE_SIMILARITY


def _years_are_compatible(first: int | None, second: int | None) -> bool:
    """年份同时存在时只允许相差一年，兼容 online-first 与正式出版年份差异。"""
    return first is None or second is None or abs(first - second) <= 1


def _first_authors_are_compatible(first: str | None, second: str | None) -> bool:
    """首位作者未知时不阻断匹配；两者已知且不同则拒绝自动合并。"""
    return first is None or second is None or first == second


def _title_similarity(first: str, second: str) -> float:
    """使用标准库字符相似度覆盖中英文标题，避免引入额外模糊匹配运行依赖。"""
    return SequenceMatcher(a=first, b=second, autojunk=False).ratio() * 100


def _build_unified_candidate(records: Sequence[NormalizedCandidateRecord]) -> UnifiedCandidate:
    """按来源优先级和字段质量选择展示值，同时完整保留来源记录与冲突。"""
    raw_records = tuple(record.raw for record in records)
    selected_doi, doi_source = _select_first(records, "doi", normalize=True)
    selected_title, title_source = _select_first(records, "title")
    selected_language, language_source = _select_first(records, "language")
    selected_authors, authors_source = _select_authors(records)
    selected_abstract, abstract_source = _select_longest_text(records, "abstract")
    selected_year, year_source = _select_first(records, "published_year")
    selected_date, date_source = _select_first(records, "published_date")
    selected_venue, venue_source = _select_first(records, "venue")
    selected_document_type, document_type_source = _select_first(records, "document_type")
    selected_volume, volume_source = _select_first(records, "volume")
    selected_issue, issue_source = _select_first(records, "issue")
    selected_pages, pages_source = _select_first(records, "pages")
    selected_article_number, article_number_source = _select_first(records, "article_number")
    selected_publisher, publisher_source = _select_first(records, "publisher")
    landing_url, landing_source = _select_first(records, "landing_url")
    open_access_url, open_access_source = _select_first(records, "open_access_url")
    fulltext_url, fulltext_source = _select_first(records, "fulltext_url")

    # RawCandidate 已保证 title 非空，因此任何内部集群都必须能得到展示标题和匹配键。
    assert isinstance(selected_title, str)
    title_key = normalize_title_key(selected_title)

    provenance = _field_provenance(
        doi=doi_source,
        title=title_source,
        language=language_source,
        authors=authors_source,
        abstract=abstract_source,
        published_year=year_source,
        published_date=date_source,
        venue=venue_source,
        document_type=document_type_source,
        volume=volume_source,
        issue=issue_source,
        pages=pages_source,
        article_number=article_number_source,
        publisher=publisher_source,
        landing_url=landing_source,
        open_access_url=open_access_source,
        fulltext_url=fulltext_source,
    )

    return UnifiedCandidate(
        doi=selected_doi if isinstance(selected_doi, str) else None,
        title=selected_title,
        title_key=title_key,
        language=(
            selected_language
            if isinstance(selected_language, CandidateLanguage)
            else infer_candidate_language(
                selected_title,
                selected_abstract if isinstance(selected_abstract, str) else None,
            )
        ),
        authors=selected_authors,
        abstract=selected_abstract if isinstance(selected_abstract, str) else None,
        published_year=selected_year if isinstance(selected_year, int) else None,
        published_date=selected_date if isinstance(selected_date, CitationDate) else None,
        venue=selected_venue if isinstance(selected_venue, str) else None,
        document_type=selected_document_type if isinstance(selected_document_type, str) else None,
        volume=selected_volume if isinstance(selected_volume, str) else None,
        issue=selected_issue if isinstance(selected_issue, str) else None,
        pages=selected_pages if isinstance(selected_pages, str) else None,
        article_number=(
            selected_article_number if isinstance(selected_article_number, str) else None
        ),
        publisher=selected_publisher if isinstance(selected_publisher, str) else None,
        citation_counts_by_source=_citation_counts(raw_records),
        links=CandidateLinks(
            landing_url=landing_url if isinstance(landing_url, str) else None,
            open_access_url=open_access_url if isinstance(open_access_url, str) else None,
            fulltext_url=fulltext_url if isinstance(fulltext_url, str) else None,
        ),
        is_open_access=_open_access_state(raw_records),
        source_records=raw_records,
        field_provenance=provenance,
        conflicts=_conflicts(records),
    )


def _ordered_records(
    records: Sequence[NormalizedCandidateRecord],
) -> list[NormalizedCandidateRecord]:
    """按正式来源优先级稳定排序，未知未来来源仍保持输入顺序排在末尾。"""
    priority = {source: index for index, source in enumerate(_FORMAL_SOURCE_PRIORITY)}
    return sorted(records, key=lambda record: priority.get(record.raw.source, len(priority)))


def _select_first(
    records: Sequence[NormalizedCandidateRecord],
    field_name: str,
    *,
    normalize: bool = False,
) -> tuple[object | None, SourceName | None]:
    """从优先来源中选取首个非空字段；DOI 选择时使用已规范化的键。"""
    for record in _ordered_records(records):
        value = record.doi_key if normalize else getattr(record.raw, field_name)

        if value is not None:
            return value, record.raw.source

    return None, None


def _select_authors(
    records: Sequence[NormalizedCandidateRecord],
) -> tuple[tuple[CandidateAuthor, ...], SourceName | None]:
    """选择优先来源中首个非空作者列表，保证展示顺序与来源一致。"""
    for record in _ordered_records(records):
        if record.raw.authors:
            return record.raw.authors, record.raw.source

    return (), None


def _select_longest_text(
    records: Sequence[NormalizedCandidateRecord],
    field_name: str,
) -> tuple[object | None, SourceName | None]:
    """摘要优先选择信息量最大的文本，长度相同时再按正式来源优先级决胜。"""
    candidates: list[tuple[str, SourceName]] = []

    for record in records:
        value = getattr(record.raw, field_name)

        if isinstance(value, str) and value.strip():
            candidates.append((value, record.raw.source))

    if not candidates:
        return None, None

    priority = {source: index for index, source in enumerate(_FORMAL_SOURCE_PRIORITY)}
    value, source = max(
        candidates,
        key=lambda item: (len(item[0]), -priority.get(item[1], len(priority))),
    )
    return value, source


def _field_provenance(**sources: SourceName | None) -> dict[str, SourceName]:
    """丢弃未选择字段的空来源，仅保留可解释的字段到来源映射。"""
    return {field_name: source for field_name, source in sources.items() if source is not None}


def _citation_counts(records: Sequence[RawCandidate]) -> dict[str, int]:
    """按来源保存引用量，不将不同统计口径压缩成虚假的单一精确数值。"""
    counts: dict[str, int] = {}

    for record in records:
        if record.citation_count is not None:
            counts[record.source.value] = record.citation_count

    return counts


def _open_access_state(records: Sequence[RawCandidate]) -> bool | None:
    """任一来源明确开放即为 True；全都未知时保留 None；其余为 False。"""
    states = {record.is_open_access for record in records if record.is_open_access is not None}

    if True in states:
        return True

    if not states:
        return None

    return False


def _conflicts(records: Sequence[NormalizedCandidateRecord]) -> dict[str, tuple[str, ...]]:
    """收集多个来源给出不同值的关键字段，供前端和后续题录核验显式呈现。"""
    values_by_field = {
        "doi": tuple(
            (normalize_doi(record.raw.doi), record.raw.doi) for record in records if record.raw.doi
        ),
        "title": tuple((record.title_key, record.raw.title) for record in records),
        "language": tuple(
            (record.raw.language.value, record.raw.language.value)
            for record in records
            if record.raw.language is not None
        ),
        "authors": tuple(
            (
                ";".join(normalize_author_key(author.name) or "" for author in record.raw.authors),
                "; ".join(author.name for author in record.raw.authors),
            )
            for record in records
            if record.raw.authors
        ),
        "published_year": tuple(
            (str(record.raw.published_year), str(record.raw.published_year))
            for record in records
            if record.raw.published_year is not None
        ),
        "published_date": tuple(
            (
                "-".join(str(part) for part in record.raw.published_date.to_csl_date_parts()),
                "-".join(str(part) for part in record.raw.published_date.to_csl_date_parts()),
            )
            for record in records
            if record.raw.published_date is not None
        ),
        "volume": tuple(
            (normalize_title_key(record.raw.volume), record.raw.volume)
            for record in records
            if record.raw.volume
        ),
        "issue": tuple(
            (normalize_title_key(record.raw.issue), record.raw.issue)
            for record in records
            if record.raw.issue
        ),
        "pages": tuple(
            (normalize_title_key(record.raw.pages), record.raw.pages)
            for record in records
            if record.raw.pages
        ),
        "article_number": tuple(
            (normalize_title_key(record.raw.article_number), record.raw.article_number)
            for record in records
            if record.raw.article_number
        ),
        "publisher": tuple(
            (normalize_title_key(record.raw.publisher), record.raw.publisher)
            for record in records
            if record.raw.publisher
        ),
        "venue": tuple(
            (normalize_title_key(record.raw.venue), record.raw.venue)
            for record in records
            if record.raw.venue
        ),
        "document_type": tuple(
            (normalize_title_key(record.raw.document_type), record.raw.document_type)
            for record in records
            if record.raw.document_type
        ),
    }
    return {
        field_name: conflict_values
        for field_name, values in values_by_field.items()
        if (conflict_values := _conflicting_values(values))
    }


def _conflicting_values(values: Sequence[tuple[str | None, str]]) -> tuple[str, ...] | None:
    """仅当规范化键不同才返回原始展示值，避免格式差异被误报为冲突。"""
    values_by_key: dict[str, str] = {}

    for key, display_value in values:
        if key is not None:
            values_by_key.setdefault(key, display_value)

    unique_values = tuple(values_by_key.values())
    return unique_values if len(unique_values) > 1 else None
