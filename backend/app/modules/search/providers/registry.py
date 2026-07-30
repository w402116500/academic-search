"""根据配置组装已实现的文献来源 Provider。"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from app.core.settings import LiteratureSourceSettings
from app.modules.search.contracts import SourceName
from app.modules.search.providers.arxiv import ArxivProvider
from app.modules.search.providers.base import SearchProvider
from app.modules.search.providers.crossref import CrossrefProvider
from app.modules.search.providers.openalex import OpenAlexProvider
from app.modules.search.providers.semantic_scholar import SemanticScholarProvider


class ProviderRegistry:
    """保存当前进程可调用的来源 Provider，并按稳定来源名索引。"""

    def __init__(self, providers: Iterable[SearchProvider]) -> None:
        """拒绝重复来源，防止同一来源被并发执行两次并污染后续去重统计。"""
        self._providers: dict[SourceName, SearchProvider] = {}

        for provider in providers:
            if provider.source in self._providers:
                raise ValueError(f"重复注册文献来源：{provider.source}")

            self._providers[provider.source] = provider

    def get(self, source: SourceName) -> SearchProvider | None:
        """按来源名取得 Provider；未实现或已禁用时返回 None。"""
        return self._providers.get(source)

    def __iter__(self) -> Iterator[SearchProvider]:
        """按注册顺序遍历 Provider，供未来编排器决定并发调度顺序。"""
        return iter(self._providers.values())

    def __len__(self) -> int:
        """返回已启用且已实现的 Provider 数量。"""
        return len(self._providers)


def build_provider_registry(settings: LiteratureSourceSettings) -> ProviderRegistry:
    """仅注册已实现且在环境变量中启用的来源。

    来源是否注册同时取决于“配置已启用”和“对应 Provider 已实现”，避免把关闭的
    外部服务放进后续并发任务。
    """
    providers: list[SearchProvider] = []

    if settings.openalex.enabled:
        providers.append(OpenAlexProvider(settings.openalex))

    if settings.crossref.enabled:
        providers.append(CrossrefProvider(settings.crossref))

    if settings.arxiv.enabled:
        providers.append(ArxivProvider(settings.arxiv))

    if settings.semantic_scholar.enabled:
        providers.append(SemanticScholarProvider(settings.semantic_scholar))

    return ProviderRegistry(providers)
