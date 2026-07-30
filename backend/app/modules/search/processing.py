"""将多个 Provider 结果处理为可排序、可展示的临时候选。"""

from __future__ import annotations

from collections.abc import Iterable

from app.modules.search.contracts import (
    CandidateProcessingResult,
    ProviderQuery,
    ProviderSearchResult,
)
from app.modules.search.deduplicate import deduplicate_candidates
from app.modules.search.normalize import normalize_raw_candidate
from app.modules.search.triage import triage_candidates


def process_provider_results(
    provider_results: Iterable[ProviderSearchResult],
    query: ProviderQuery,
) -> CandidateProcessingResult:
    """执行候选合并和初筛，并保留独立 Provider 的失败信息。

    调用方可以并发请求来源后将所有结果传入此函数。即使某个来源失败，其他来源
    的候选仍能继续进入处理链路，因此这里不抛出来源级异常。
    """
    results = tuple(provider_results)
    source_candidates = tuple(candidate for result in results for candidate in result.candidates)
    normalized_candidates = tuple(
        normalize_raw_candidate(candidate) for candidate in source_candidates
    )
    deduplicated_candidates = deduplicate_candidates(normalized_candidates)
    triaged_candidates = triage_candidates(deduplicated_candidates, query)
    provider_errors = {
        result.provider: result.error for result in results if result.error is not None
    }
    included_candidate_count = sum(
        candidate.triage.included
        for candidate in triaged_candidates
        if candidate.triage is not None
    )

    return CandidateProcessingResult(
        candidates=tuple(triaged_candidates),
        provider_errors=provider_errors,
        raw_candidate_count=len(source_candidates),
        deduplicated_candidate_count=len(deduplicated_candidates),
        included_candidate_count=included_candidate_count,
    )
