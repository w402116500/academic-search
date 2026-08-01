"""检索执行器的来源并发和失败隔离测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from app.core.settings import LiteratureSourceSettings
from app.db.models.workflow import SearchRun
from app.modules.search.contracts import (
    ProviderError,
    ProviderErrorCode,
    ProviderQuery,
    ProviderSearchResult,
    RawCandidate,
    SourceName,
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


def test_session_store_keys_are_scoped_to_the_run() -> None:
    """执行器接收的会话键必须是服务端按运行 UUID 生成的键。"""
    assert _run().redis_session_key == "academic-search:search-run:test"
