"""OpenAlex Provider 的离线字段转换和失败语义测试。"""

import json
from pathlib import Path

import httpx
import pytest
from app.core.settings import LiteratureSourceSettings
from app.modules.search.contracts import ProviderErrorCode, ProviderQuery, SourceName
from app.modules.search.providers.openalex import OpenAlexProvider
from app.modules.search.providers.registry import build_provider_registry
from pydantic import SecretStr

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "openalex_works_page.json"


def make_openalex_settings() -> LiteratureSourceSettings:
    """构造不依赖本地 .env 的测试配置，防止真实密钥进入测试断言。"""
    return LiteratureSourceSettings(
        app_env="test",
        openalex_api_key=SecretStr("test-openalex-key"),
        openalex_contact_email="developer@example.com",
        crossref_enabled=False,
        semantic_scholar_enabled=False,
    )


@pytest.mark.asyncio
async def test_openalex_provider_maps_candidates_and_restores_abstract() -> None:
    """Provider 应保留作者顺序、开放获取地址和按位置还原的摘要文本。"""
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    observed_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """模拟 OpenAlex，并记录请求参数以验证认证与年份过滤映射。"""
        observed_params.update(dict(request.url.params))
        assert request.url.path == "/works"
        return httpx.Response(200, json=payload)

    provider = OpenAlexProvider(
        make_openalex_settings().openalex,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.search(
        ProviderQuery(
            query="large language models academic writing",
            limit=25,
            from_publication_year=2021,
            to_publication_year=2024,
        )
    )

    assert result.error is None
    assert result.provider is SourceName.OPENALEX
    assert len(result.candidates) == 1
    assert observed_params["api_key"] == "test-openalex-key"
    assert observed_params["mailto"] == "developer@example.com"
    assert (
        observed_params["filter"]
        == "from_publication_date:2021-01-01,to_publication_date:2024-12-31"
    )

    candidate = result.candidates[0]
    assert candidate.source_record_id == "W1234567890"
    assert [author.name for author in candidate.authors] == ["Ada Lovelace", "Alan Turing"]
    assert candidate.abstract == "Large language models support academic writing"
    assert candidate.venue == "Journal of Academic AI"
    assert candidate.published_date is not None
    assert candidate.published_date.to_csl_date_parts() == [2024, 5, 1]
    assert candidate.volume == "12"
    assert candidate.issue == "3"
    assert candidate.pages == "101-115"
    assert candidate.open_access_url == "https://repository.example.org/article/1"
    assert candidate.fulltext_url == "https://repository.example.org/article/1.pdf"


@pytest.mark.asyncio
async def test_openalex_provider_marks_rate_limit_as_retryable() -> None:
    """来源限流应转换为可恢复错误，而不是泄露响应正文或抛出到调用方。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """返回限流状态，用于验证 Provider 的安全错误映射。"""
        return httpx.Response(429, text="private upstream response")

    provider = OpenAlexProvider(
        make_openalex_settings().openalex,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.search(ProviderQuery(query="academic writing"))

    assert result.candidates == ()
    assert result.error is not None
    assert result.error.code is ProviderErrorCode.REMOTE_ERROR
    assert result.error.http_status_code == 429
    assert result.error.retryable is True
    assert "private upstream response" not in result.error.message


def test_provider_registry_only_registers_enabled_implemented_providers() -> None:
    """未实现来源即使配置已启用，也不能被伪装为可供编排器调用。"""
    settings = make_openalex_settings()
    registry = build_provider_registry(settings)

    assert len(registry) == 1
    assert registry.get(SourceName.OPENALEX) is not None
    assert registry.get(SourceName.CROSSREF) is None
