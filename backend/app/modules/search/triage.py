"""多来源合并候选的低成本、可解释规则初筛。"""

from __future__ import annotations

import re

from app.modules.search.contracts import (
    ProviderQuery,
    SourceName,
    TriageDecision,
    TriageReasonCode,
    UnifiedCandidate,
)

# 只排除明显不是研究论文的记录；未知类型保留给后续语义评估，避免来源差异导致误删。
_EXCLUDED_DOCUMENT_TYPES = {
    "book",
    "bookchapter",
    "correction",
    "dataset",
    "dissertation",
    "editorial",
    "grant",
    "peerreview",
    "referenceentry",
    "retraction",
}
_FORMAL_METADATA_SOURCES = {
    SourceName.OPENALEX,
    SourceName.CROSSREF,
    SourceName.SEMANTIC_SCHOLAR,
}
_NON_WORD_PATTERN = re.compile(r"[^\w]+", re.UNICODE)


def triage_candidate(candidate: UnifiedCandidate, query: ProviderQuery) -> UnifiedCandidate:
    """生成单条候选的初筛结论，并以不可变副本返回避免隐式修改上游结果。"""
    exclusion_reasons: list[TriageReasonCode] = []
    warnings: list[TriageReasonCode] = []

    if not candidate.title.strip():
        exclusion_reasons.append(TriageReasonCode.MISSING_TITLE)

    if _normalized_document_type(candidate.document_type) in _EXCLUDED_DOCUMENT_TYPES:
        exclusion_reasons.append(TriageReasonCode.UNSUPPORTED_DOCUMENT_TYPE)

    if not _matches_year_range(candidate.published_year, query):
        exclusion_reasons.append(TriageReasonCode.YEAR_OUT_OF_RANGE)

    source_names = {record.source for record in candidate.source_records}

    if SourceName.ARXIV in source_names and not source_names.intersection(_FORMAL_METADATA_SOURCES):
        warnings.append(TriageReasonCode.PREPRINT_ONLY)

    if candidate.abstract is None:
        warnings.append(TriageReasonCode.MISSING_ABSTRACT)

    if candidate.doi is None:
        warnings.append(TriageReasonCode.MISSING_DOI)

    if candidate.conflicts:
        warnings.append(TriageReasonCode.METADATA_CONFLICT)

    decision = TriageDecision(
        included=not exclusion_reasons,
        exclusion_reasons=tuple(exclusion_reasons),
        warnings=tuple(warnings),
    )
    return candidate.model_copy(update={"triage": decision})


def triage_candidates(
    candidates: list[UnifiedCandidate],
    query: ProviderQuery,
) -> list[UnifiedCandidate]:
    """按输入顺序初筛全部候选；排除项仍保留在结果中以支持进度和审计说明。"""
    return [triage_candidate(candidate, query) for candidate in candidates]


def _normalized_document_type(value: str | None) -> str | None:
    """统一来源不同的连字符、下划线与大小写形式，便于判断显然不支持的类型。"""
    if value is None:
        return None

    normalized = _NON_WORD_PATTERN.sub("", value.casefold())
    return normalized or None


def _matches_year_range(year: int | None, query: ProviderQuery) -> bool:
    """来源缺年份时不排除；有年份时必须满足用户明确设定的范围。"""
    if year is None:
        return True

    if query.from_publication_year is not None and year < query.from_publication_year:
        return False

    if query.to_publication_year is not None and year > query.to_publication_year:
        return False

    return True
