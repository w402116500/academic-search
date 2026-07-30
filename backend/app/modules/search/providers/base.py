"""外部文献来源适配器的统一协议。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.modules.search.contracts import ProviderQuery, ProviderSearchResult, SourceName


@runtime_checkable
class SearchProvider(Protocol):
    """一个可被搜索编排器调用的外部文献来源。"""

    # 来源名称让编排器、日志和 SSE 事件可以使用稳定标识，而非 Python 类名。
    source: SourceName

    async def search(self, query: ProviderQuery) -> ProviderSearchResult:
        """执行一次候选召回，并将可恢复错误转换为 ProviderSearchResult。"""
        raise NotImplementedError
