"""真实文献源到统一候选结果的手动集成测试。

本测试只在显式设置 ``RUN_LIVE_SEARCH_TESTS=1`` 时运行。它会消耗外部来源配额，
因此默认跳过，避免常规单元测试、预提交和 CI 因网络、限流或 API 维护而不稳定。
设置 ``LIVE_ENABLE_SEMANTIC_SCHOLAR=1`` 可仅在本次测试中临时启用该来源。
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from app.core.settings import get_literature_source_settings
from app.modules.search.contracts import ProviderQuery, UnifiedCandidate
from app.modules.search.processing import process_provider_results
from app.modules.search.providers.registry import build_provider_registry

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_SEARCH_TESTS"
_SEMANTIC_SCHOLAR_LIVE_OVERRIDE_FLAG = "LIVE_ENABLE_SEMANTIC_SCHOLAR"
_DEFAULT_QUERY = "large language models academic writing"
_DEFAULT_RESULT_LIMIT = 2


def _live_test_is_enabled() -> bool:
    """只有用户主动打开环境变量时才允许真实请求外部学术 API。"""
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


def _live_result_limit() -> int:
    """读取可选的实时测试召回上限，并限制在低成本的安全范围内。"""
    raw_limit = os.getenv("LIVE_SEARCH_RESULT_LIMIT", str(_DEFAULT_RESULT_LIMIT))

    try:
        result_limit = int(raw_limit)
    except ValueError as exc:
        raise pytest.UsageError("LIVE_SEARCH_RESULT_LIMIT 必须是整数") from exc

    if not 1 <= result_limit <= 5:
        raise pytest.UsageError("LIVE_SEARCH_RESULT_LIMIT 必须位于 1 到 5 之间")

    return result_limit


def _settings_for_live_test():
    """读取来源配置，并可仅为本次真实测试临时启用 Semantic Scholar。"""
    settings = get_literature_source_settings()

    if os.getenv(_SEMANTIC_SCHOLAR_LIVE_OVERRIDE_FLAG) != "1":
        return settings

    semantic_scholar = settings.semantic_scholar

    if semantic_scholar.auth_token is None:
        required_variable = (
            "S2API_OMINIAI_API_KEY"
            if semantic_scholar.access_mode == "ominiai"
            else "SEMANTIC_SCHOLAR_API_KEY"
        )
        pytest.skip(f"临时启用 Semantic Scholar 需要在 .env 配置 {required_variable}")

    # model_copy 不修改缓存的全局设置对象，避免本测试泄漏状态到同一 pytest 进程的其他测试。
    return settings.model_copy(update={"semantic_scholar_enabled": True})


def _provider_connection_modes(settings) -> dict[str, dict[str, str]]:
    """输出不含密钥的来源访问诊断，便于区分网关问题与网络代理问题。"""
    modes = {
        "openalex": {"network_mode": settings.openalex.network.mode},
        "crossref": {"network_mode": settings.crossref.network.mode},
        "arxiv": {"network_mode": settings.arxiv.network.mode},
        "semantic_scholar": {
            "network_mode": settings.semantic_scholar.network.mode,
            "access_mode": settings.semantic_scholar.access_mode,
        },
    }
    return modes


def _candidate_summary(candidate: UnifiedCandidate) -> dict[str, object]:
    """构造可安全打印的统一候选摘要，避免输出 API Key 或完整长摘要。"""
    # 通过属性读取而非 model_dump，明确列出实时验收关心的最终交付字段。
    return {
        "candidate_id": str(candidate.candidate_id),
        "doi": candidate.doi,
        "title": candidate.title,
        "language": candidate.language.value,
        "authors": [author.name for author in candidate.authors[:3]],
        "author_count": len(candidate.authors),
        "published_year": candidate.published_year,
        "venue": candidate.venue,
        "document_type": candidate.document_type,
        "abstract_length": len(candidate.abstract) if candidate.abstract else 0,
        "citation_counts_by_source": candidate.citation_counts_by_source,
        "links": candidate.links.model_dump(),
        "source_records": [
            {
                "source": record.source.value,
                "source_record_id": record.source_record_id,
            }
            for record in candidate.source_records
        ],
        "field_provenance": {
            field_name: source.value for field_name, source in candidate.field_provenance.items()
        },
        "conflicts": candidate.conflicts,
        "triage": candidate.triage.model_dump(mode="json") if candidate.triage else None,
    }


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_search_pipeline_returns_unified_candidates() -> None:
    """真实 Provider 结果应经过处理链路并形成带来源与初筛结论的统一候选。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实文献源测试")

    semantic_scholar_live_override = os.getenv(_SEMANTIC_SCHOLAR_LIVE_OVERRIDE_FLAG) == "1"
    settings = _settings_for_live_test()
    providers = list(build_provider_registry(settings))
    query = ProviderQuery(
        query=os.getenv("LIVE_SEARCH_QUERY", _DEFAULT_QUERY),
        limit=_live_result_limit(),
    )

    # 各来源独立执行；单个来源的网络错误会进入 ProviderSearchResult，不阻断其他来源。
    provider_results = await asyncio.gather(*(provider.search(query) for provider in providers))
    processed = process_provider_results(provider_results, query)
    successful_providers = [
        result.provider.value for result in provider_results if result.error is None
    ]
    summary = {
        "query": query.query,
        "semantic_scholar_live_override": semantic_scholar_live_override,
        "enabled_providers": [provider.source.value for provider in providers],
        "provider_connection_modes": _provider_connection_modes(settings),
        "successful_providers": successful_providers,
        "provider_errors": {
            provider.value: error.model_dump(mode="json")
            for provider, error in processed.provider_errors.items()
        },
        "raw_candidate_count": processed.raw_candidate_count,
        "deduplicated_candidate_count": processed.deduplicated_candidate_count,
        "included_candidate_count": processed.included_candidate_count,
        "candidates": [_candidate_summary(candidate) for candidate in processed.candidates],
    }

    # 使用 ASCII 转义避免 Windows 的 GBK 终端因作者名中的特殊字符而中断测试。
    # 用 ``-s`` 执行 pytest 时，仍可直接查看最终 UnifiedCandidate 的完整字段形态。
    print(json.dumps(summary, ensure_ascii=True, indent=2))

    assert providers, "当前 .env 没有启用任何文献来源"
    assert successful_providers, "所有启用来源都请求失败，请检查网络、代理、Key 和限流状态"
    assert processed.candidates, "来源请求成功但没有返回可处理的候选文献"
    assert all(candidate.source_records for candidate in processed.candidates)
    assert all(candidate.triage is not None for candidate in processed.candidates)
