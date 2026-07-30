"""文献源环境变量解析与配置校验测试。"""

import pytest
from app.core.settings import LiteratureSourceSettings
from pydantic import SecretStr, ValidationError


def test_development_allows_openalex_without_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """开发环境可用公开接口调试，但生产环境会强制要求 OpenAlex API Key。"""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("OPENALEX_ENABLED", "true")
    monkeypatch.setenv("SEMANTIC_SCHOLAR_ENABLED", "false")
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    # 直接构造设置对象，确保此测试不会因根目录 .env 中的真实 Key 而受到影响。
    settings = LiteratureSourceSettings()

    assert settings.openalex.enabled is True
    assert settings.openalex.api_key is None


def test_production_requires_openalex_api_key_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """生产环境不能意外以无认证方式运行主文献来源。"""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENALEX_ENABLED", "true")
    monkeypatch.setenv("SEMANTIC_SCHOLAR_ENABLED", "false")
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="OPENALEX_API_KEY"):
        LiteratureSourceSettings()


def test_openalex_page_size_cannot_exceed_result_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """分页大小大于召回总上限没有意义，应在进程启动前直接阻止。"""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("OPENALEX_PAGE_SIZE", "51")
    monkeypatch.setenv("OPENALEX_MAX_RESULTS", "50")
    monkeypatch.setenv("SEMANTIC_SCHOLAR_ENABLED", "false")
    with pytest.raises(ValidationError, match="OPENALEX_PAGE_SIZE"):
        LiteratureSourceSettings()


def test_provider_network_routes_can_be_configured_independently() -> None:
    """每个来源应独立选择直连或显式代理，而不是继承进程全局代理变量。"""
    settings = LiteratureSourceSettings(
        app_env="test",
        literature_proxy_url="http://127.0.0.1:7897",
        openalex_network_mode="proxy",
        crossref_network_mode="direct",
        arxiv_network_mode="direct",
        semantic_scholar_network_mode="direct",
        doi_resolver_network_mode="direct",
    )

    assert settings.openalex.network.mode == "proxy"
    assert settings.openalex.network.proxy_url == "http://127.0.0.1:7897"
    assert settings.crossref.network.mode == "direct"
    assert settings.crossref.network.proxy_url is None
    assert settings.arxiv.network.mode == "direct"
    assert settings.semantic_scholar.network.mode == "direct"
    assert settings.doi_resolver.network.mode == "direct"
    assert settings.doi_resolver.network.proxy_url is None


def test_proxy_route_requires_an_explicit_proxy_url() -> None:
    """启用代理但未提供地址必须在加载配置阶段失败，而非请求时静默直连。"""
    with pytest.raises(ValidationError, match="LITERATURE_PROXY_URL"):
        LiteratureSourceSettings(app_env="test", openalex_network_mode="proxy")


def test_ominiai_access_mode_resolves_bearer_credentials() -> None:
    """Ominiai 通道应使用独立 Base URL 和 Bearer Token，不复用官方 API Key。"""
    settings = LiteratureSourceSettings(
        app_env="test",
        semantic_scholar_enabled=True,
        semantic_scholar_access_mode="ominiai",
        s2api_ominiai_api_key=SecretStr("test-ominiai-token"),
    )

    semantic_scholar = settings.semantic_scholar

    assert semantic_scholar.access_mode == "ominiai"
    assert semantic_scholar.base_url == "https://s2api.ominiai.cn/s2/graph/v1"
    assert semantic_scholar.auth_mode == "bearer"
    assert semantic_scholar.auth_token is not None
    assert semantic_scholar.auth_token.get_secret_value() == "test-ominiai-token"


def test_enabled_ominiai_access_mode_requires_its_own_token() -> None:
    """兼容网关没有匿名调用契约，缺少 Token 时应阻止 Provider 注册。"""
    with pytest.raises(ValidationError, match="S2API_OMINIAI_API_KEY"):
        LiteratureSourceSettings(
            app_env="test",
            semantic_scholar_enabled=True,
            semantic_scholar_access_mode="ominiai",
        )
