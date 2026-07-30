"""Crossref、arXiv 与 Semantic Scholar Provider 的离线转换测试。"""

import json
from pathlib import Path

import httpx
import pytest
from app.core.settings import LiteratureSourceSettings
from app.modules.search.contracts import ProviderQuery, SourceName
from app.modules.search.providers.arxiv import ArxivProvider
from app.modules.search.providers.crossref import CrossrefProvider
from app.modules.search.providers.registry import build_provider_registry
from app.modules.search.providers.semantic_scholar import SemanticScholarProvider
from pydantic import SecretStr

FIXTURES_DIRECTORY = Path(__file__).parents[1] / "fixtures"


def make_source_settings() -> LiteratureSourceSettings:
    """构造所有来源均启用的测试配置，不读取本地环境变量中的真实密钥。"""
    return LiteratureSourceSettings(
        app_env="test",
        openalex_enabled=True,
        openalex_api_key=SecretStr("test-openalex-key"),
        crossref_enabled=True,
        arxiv_enabled=True,
        semantic_scholar_enabled=True,
        semantic_scholar_api_key=SecretStr("test-semantic-scholar-key"),
    )


@pytest.mark.asyncio
async def test_crossref_provider_maps_bibliographic_fields() -> None:
    """Crossref 的 JATS 摘要、作者、日期数组和 PDF 链接应映射为统一候选字段。"""
    payload = json.loads(
        (FIXTURES_DIRECTORY / "crossref_works_page.json").read_text(encoding="utf-8")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        """模拟 Crossref Works API，并断言日期范围被转换为 filter 参数。"""
        assert request.url.path == "/works"
        assert request.url.params["query.bibliographic"] == "academic writing"
        assert request.url.params["filter"] == "from-pub-date:2020-01-01,until-pub-date:2024-12-31"
        return httpx.Response(200, json=payload)

    provider = CrossrefProvider(
        make_source_settings().crossref,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.search(
        ProviderQuery(
            query="academic writing",
            from_publication_year=2020,
            to_publication_year=2024,
        )
    )

    assert result.error is None
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.source is SourceName.CROSSREF
    assert candidate.doi == "10.1000/crossref.example"
    assert candidate.authors[0].name == "Ada Lovelace"
    assert candidate.abstract == "Evidence from a controlled study."
    assert candidate.published_year == 2024
    assert candidate.published_date is not None
    assert candidate.published_date.to_csl_date_parts() == [2024, 5, 1]
    assert candidate.volume == "12"
    assert candidate.issue == "3"
    assert candidate.pages == "101-115"
    assert candidate.article_number == "e102274"
    assert candidate.publisher == "Academic Press"
    assert candidate.fulltext_url == "https://publisher.example.org/article.pdf"
    assert candidate.is_open_access is None


@pytest.mark.asyncio
async def test_arxiv_provider_maps_atom_entry_as_open_preprint() -> None:
    """arXiv Atom 条目必须保留预印本性质、公开 PDF 与清理后的换行文本。"""
    feed = (FIXTURES_DIRECTORY / "arxiv_feed.xml").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        """模拟 arXiv 查询接口，并验证查询词由 Provider 构造成短语搜索。"""
        assert request.url.path == "/api/query"
        assert request.url.params["search_query"] == 'all:"academic writing"'
        assert request.headers["user-agent"] == "academic-search/0.1.0"
        return httpx.Response(200, text=feed, headers={"Content-Type": "application/atom+xml"})

    provider = ArxivProvider(
        make_source_settings().arxiv,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.search(
        ProviderQuery(
            query="academic writing",
            from_publication_year=2020,
            to_publication_year=2024,
        )
    )

    assert result.error is None
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.source is SourceName.ARXIV
    assert candidate.source_record_id == "2401.01234v2"
    assert candidate.title == "Large Language Models for Academic Writing"
    assert candidate.abstract == "This paper studies writing support."
    assert candidate.document_type == "preprint"
    assert candidate.is_open_access is True
    assert candidate.fulltext_url == "http://arxiv.org/pdf/2401.01234v2"


@pytest.mark.asyncio
async def test_semantic_scholar_provider_maps_keyed_graph_response() -> None:
    """Semantic Scholar 应把 API Key 放入请求头，并映射 DOI、引用量和开放 PDF。"""
    payload = json.loads(
        (FIXTURES_DIRECTORY / "semantic_scholar_search.json").read_text(encoding="utf-8")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        """模拟 Graph API，并验证 Key 没有进入 URL 查询参数。"""
        assert request.url.path == "/graph/v1/paper/search"
        assert request.headers["x-api-key"] == "test-semantic-scholar-key"
        assert "test-semantic-scholar-key" not in str(request.url)
        return httpx.Response(200, json=payload)

    provider = SemanticScholarProvider(
        make_source_settings().semantic_scholar,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.search(ProviderQuery(query="academic writing", limit=10))

    assert result.error is None
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.source is SourceName.SEMANTIC_SCHOLAR
    assert candidate.doi == "10.1000/semantic.example"
    assert candidate.citation_count == 18
    assert candidate.document_type == "JournalArticle"
    assert candidate.published_date is not None
    assert candidate.published_date.to_csl_date_parts() == [2022, 6, 15]
    assert candidate.venue == "Semantic Research Journal"
    assert candidate.volume == "8"
    assert candidate.issue == "2"
    assert candidate.pages == "33-49"
    assert candidate.fulltext_url == "https://repository.example.org/semantic-paper.pdf"
    assert candidate.is_open_access is True


@pytest.mark.asyncio
async def test_semantic_scholar_ominiai_channel_uses_bearer_token() -> None:
    """Ominiai 通道应复用 Graph 响应映射，但必须使用自己的 Base URL 与鉴权方式。"""
    payload = json.loads(
        (FIXTURES_DIRECTORY / "semantic_scholar_search.json").read_text(encoding="utf-8")
    )
    settings = LiteratureSourceSettings(
        app_env="test",
        openalex_enabled=False,
        crossref_enabled=False,
        arxiv_enabled=False,
        semantic_scholar_enabled=True,
        semantic_scholar_access_mode="ominiai",
        s2api_ominiai_api_key=SecretStr("test-ominiai-token"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        """验证 Ominiai 的兼容路径与 Bearer 鉴权，不允许令牌进入 URL。"""
        assert request.url.path == "/s2/graph/v1/paper/search"
        assert request.headers["authorization"] == "Bearer test-ominiai-token"
        assert "x-api-key" not in request.headers
        assert "test-ominiai-token" not in str(request.url)
        return httpx.Response(200, json=payload)

    provider = SemanticScholarProvider(
        settings.semantic_scholar,
        transport=httpx.MockTransport(handler),
    )
    result = await provider.search(ProviderQuery(query="academic writing", limit=10))

    assert result.error is None
    assert len(result.candidates) == 1
    assert result.candidates[0].source is SourceName.SEMANTIC_SCHOLAR


def test_registry_registers_every_enabled_implemented_provider() -> None:
    """所有来源启用时，注册表应提供四个不重复的适配器。"""
    registry = build_provider_registry(make_source_settings())

    assert len(registry) == 4
    assert {provider.source for provider in registry} == {
        SourceName.OPENALEX,
        SourceName.CROSSREF,
        SourceName.ARXIV,
        SourceName.SEMANTIC_SCHOLAR,
    }


@pytest.mark.asyncio
async def test_arxiv_provider_ignores_process_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """直连 arXiv 时，HTTP 客户端必须关闭 trust_env 并显式传入空代理。"""
    observed_client_options: list[dict[str, object]] = []

    class RecordingAsyncClient:
        """记录客户端配置后模拟网络错误，避免测试触碰真实网络。"""

        def __init__(self, **kwargs: object) -> None:
            observed_client_options.append(kwargs)

        async def __aenter__(self) -> "RecordingAsyncClient":
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
            request = httpx.Request("GET", "https://export.arxiv.org/api/query")
            raise httpx.ConnectError("offline test transport", request=request)

    monkeypatch.setattr(httpx, "AsyncClient", RecordingAsyncClient)
    provider = ArxivProvider(make_source_settings().arxiv)

    result = await provider.search(ProviderQuery(query="academic writing", limit=1))

    assert len(observed_client_options) == 1
    client_options = observed_client_options[0]
    assert client_options["base_url"] == ""
    assert client_options["proxy"] is None
    assert client_options["trust_env"] is False
    assert result.error is not None
