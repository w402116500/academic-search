"""DOI Content Negotiation 解析器的离线行为测试。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from app.core.settings import LiteratureSourceSettings
from app.modules.literature.contracts import CitationResolutionErrorCode
from app.modules.search.providers.doi_resolver import DoiMetadataResolver

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "doi_csl_record.json"


def _settings() -> LiteratureSourceSettings:
    """创建与本地 .env 隔离的 DOI 解析配置，确保断言不依赖真实网络。"""
    return LiteratureSourceSettings(
        app_env="test",
        openalex_enabled=False,
        crossref_enabled=False,
        arxiv_enabled=False,
        semantic_scholar_enabled=False,
        doi_resolver_network_mode="direct",
    )


@pytest.mark.asyncio
async def test_doi_resolver_maps_csl_json_to_authoritative_metadata() -> None:
    """成功响应必须保留结构化作者、完整日期和卷期页等题录字段。"""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        """验证内容协商请求只携带所需 Accept 头和规范 DOI 路径。"""
        assert request.url.path == "/10.1000/crossref.example"
        assert request.headers["accept"] == "application/vnd.citationstyles.csl+json"
        return httpx.Response(200, json=payload)

    resolver = DoiMetadataResolver(
        _settings().doi_resolver,
        transport=httpx.MockTransport(handler),
    )
    result = await resolver.resolve(" DOI: https://doi.org/10.1000/Crossref.Example. ")

    assert result.error is None
    assert result.record is not None
    assert result.doi == "10.1000/crossref.example"
    assert result.record.authors[0].given == "Ada"
    assert result.record.authors[0].family == "Lovelace"
    assert result.record.issued_date is not None
    assert result.record.issued_date.to_csl_date_parts() == [2024, 5, 1]
    assert result.record.volume == "12"
    assert result.record.issue == "3"
    assert result.record.pages == "101-115"
    assert result.record.article_number == "e102274"
    assert result.record.publisher == "Academic Press"


@pytest.mark.asyncio
async def test_doi_resolver_returns_explicit_non_retryable_not_found_error() -> None:
    """DOI 未注册时保留 404 状态，不泄露上游正文，也不建议无意义重试。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """模拟上游的私有错误正文，验证其不会进入错误消息。"""
        return httpx.Response(404, text="private upstream body")

    resolver = DoiMetadataResolver(
        _settings().doi_resolver,
        transport=httpx.MockTransport(handler),
    )
    result = await resolver.resolve("10.1000/missing")

    assert result.record is None
    assert result.error is not None
    assert result.error.code is CitationResolutionErrorCode.REMOTE_ERROR
    assert result.error.http_status_code == 404
    assert result.error.retryable is False
    assert "private upstream body" not in result.error.message


@pytest.mark.asyncio
async def test_doi_resolver_marks_timeout_as_retryable() -> None:
    """网络超时必须与字段缺失区分开，让调用方能提供明确的重试动作。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """模拟无响应网络连接，不访问真实 DOI 服务。"""
        raise httpx.ReadTimeout("offline timeout", request=request)

    resolver = DoiMetadataResolver(
        _settings().doi_resolver,
        transport=httpx.MockTransport(handler),
    )
    result = await resolver.resolve("10.1000/timeout")

    assert result.record is None
    assert result.error is not None
    assert result.error.code is CitationResolutionErrorCode.TIMEOUT
    assert result.error.retryable is True
