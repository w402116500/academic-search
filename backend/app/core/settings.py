"""文献源配置的类型化读取与校验。

外部学术 API 的地址、密钥和限流参数都只从环境变量读取。Provider 不直接
调用 ``os.getenv``，以便在测试时注入配置，也避免不同来源采用不一致的默认值。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.env import load_env

AppEnvironment = Literal["development", "test", "production"]
NetworkMode = Literal["direct", "proxy"]
SemanticScholarAccessMode = Literal["official", "ominiai"]
SemanticScholarAuthMode = Literal["x_api_key", "bearer"]


class ProviderNetworkSettings(BaseModel):
    """单个文献来源的网络路由配置。

    ``proxy_url`` 只会在 ``mode=proxy`` 时传给 HTTP 客户端。将其保留在
    Provider 配置内，可以避免某个来源意外继承进程级 HTTP_PROXY 环境变量。
    """

    mode: NetworkMode
    proxy_url: str | None


class OpenAlexProviderSettings(BaseModel):
    """OpenAlex Provider 运行时需要的最小配置。

    该对象由 :class:`LiteratureSourceSettings` 创建，业务代码只依赖这个小范围
    配置，避免 Provider 意外接触数据库或模型服务的环境变量。
    """

    enabled: bool
    base_url: str
    api_key: SecretStr | None
    contact_email: str | None
    request_timeout_seconds: float
    page_size: int
    max_results: int
    rate_limit_per_minute: int
    network: ProviderNetworkSettings


class CrossrefProviderSettings(BaseModel):
    """Crossref Provider 运行时需要的最小配置。"""

    enabled: bool
    base_url: str
    contact_email: str | None
    request_timeout_seconds: float
    page_size: int
    max_results: int
    rate_limit_per_minute: int
    network: ProviderNetworkSettings


class ArxivProviderSettings(BaseModel):
    """arXiv Provider 运行时需要的最小配置。"""

    enabled: bool
    base_url: str
    request_timeout_seconds: float
    max_results: int
    min_request_interval_seconds: float
    contact_email: str | None
    network: ProviderNetworkSettings


class SemanticScholarProviderSettings(BaseModel):
    """Semantic Scholar Provider 运行时需要的最小配置。"""

    enabled: bool
    base_url: str
    access_mode: SemanticScholarAccessMode
    auth_mode: SemanticScholarAuthMode
    auth_token: SecretStr | None
    request_timeout_seconds: float
    max_results: int
    rate_limit_per_minute: int
    network: ProviderNetworkSettings


class DoiResolverSettings(BaseModel):
    """DOI Content Negotiation 解析器的运行配置。"""

    base_url: str
    request_timeout_seconds: float
    network: ProviderNetworkSettings


class LiteratureSourceSettings(BaseSettings):
    """从环境变量加载文献源、网络访问和题录核验设置。"""

    # ``load_env`` 已负责加载仓库根目录的 .env，因此这里不再声明第二个 env_file。
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # 开发、测试和生产环境的校验要求不同，生产环境不能无密钥启用 OpenAlex。
    app_env: AppEnvironment = "development"

    # 这些公共限制由未来的搜索编排器使用，Provider 当前只使用 HTTP 超时配置。
    search_http_timeout_seconds: float = Field(default=10, gt=0, le=60)
    search_max_concurrent_providers: int = Field(default=3, ge=1, le=10)
    search_session_ttl_seconds: int = Field(default=7200, ge=60, le=86400)
    search_provider_cache_ttl_seconds: int = Field(default=1800, ge=0, le=86400)

    # 网络路由只影响外部 HTTP 请求，不决定数据源使用官方 API 还是兼容网关。
    literature_default_network_mode: NetworkMode = "direct"
    literature_proxy_url: str | None = None

    # OpenAlex 是首个实现的主候选召回来源。
    openalex_enabled: bool = True
    openalex_base_url: str = "https://api.openalex.org"
    openalex_api_key: SecretStr | None = None
    openalex_contact_email: str | None = None
    openalex_page_size: int = Field(default=25, ge=1, le=200)
    openalex_max_results: int = Field(default=50, ge=1, le=200)
    openalex_rate_limit_per_minute: int = Field(default=60, ge=1)
    # None 表示继承 LITERATURE_DEFAULT_NETWORK_MODE。
    openalex_network_mode: NetworkMode | None = None

    # Crossref 的配置先纳入统一契约，具体 Provider 在下一阶段实现。
    crossref_enabled: bool = True
    crossref_base_url: str = "https://api.crossref.org"
    crossref_contact_email: str | None = None
    crossref_page_size: int = Field(default=25, ge=1, le=1000)
    crossref_max_results: int = Field(default=50, ge=1, le=1000)
    crossref_rate_limit_per_minute: int = Field(default=50, ge=1)
    crossref_network_mode: NetworkMode | None = None

    # arXiv 用于预印本发现；它不应单独承担正式题录依据。
    arxiv_enabled: bool = False
    arxiv_base_url: str = "https://export.arxiv.org/api/query"
    arxiv_max_results: int = Field(default=30, ge=1, le=300)
    arxiv_min_request_interval_seconds: float = Field(default=3, gt=0, le=60)
    arxiv_contact_email: str | None = None
    arxiv_network_mode: NetworkMode | None = None

    semantic_scholar_enabled: bool = False
    # official 使用官方 API；ominiai 使用通过实测的 S2API 兼容网关。
    semantic_scholar_access_mode: SemanticScholarAccessMode = "official"
    semantic_scholar_base_url: str = "https://api.semanticscholar.org/graph/v1"
    semantic_scholar_api_key: SecretStr | None = None
    s2api_ominiai_base_url: str = "https://s2api.ominiai.cn/s2/graph/v1"
    s2api_ominiai_api_key: SecretStr | None = None
    semantic_scholar_max_results: int = Field(default=30, ge=1, le=100)
    semantic_scholar_rate_limit_per_minute: int = Field(default=30, ge=1)
    semantic_scholar_network_mode: NetworkMode | None = None

    # DOI 服务只在用户复制正式题录或加入研究集合时按需调用。
    doi_resolver_base_url: str = "https://doi.org"
    doi_resolver_timeout_seconds: float = Field(default=10, gt=0, le=60)
    # None 表示继承 LITERATURE_DEFAULT_NETWORK_MODE。
    doi_resolver_network_mode: NetworkMode | None = None

    @field_validator(
        "openalex_api_key",
        "semantic_scholar_api_key",
        "s2api_ominiai_api_key",
        "openalex_contact_email",
        "crossref_contact_email",
        "arxiv_contact_email",
        "literature_proxy_url",
        "openalex_network_mode",
        "crossref_network_mode",
        "arxiv_network_mode",
        "semantic_scholar_network_mode",
        "doi_resolver_network_mode",
        mode="before",
    )
    @classmethod
    def empty_optional_values_are_none(cls, value: object) -> object:
        """将空字符串统一为 None，避免空凭据或空路由被误认为有效配置。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "openalex_base_url",
        "crossref_base_url",
        "arxiv_base_url",
        "semantic_scholar_base_url",
        "s2api_ominiai_base_url",
        "doi_resolver_base_url",
    )
    @classmethod
    def base_url_must_be_absolute_http_url(cls, value: str) -> str:
        """拒绝相对地址和非 HTTP(S) 协议，并去掉末尾斜杠便于安全拼接路径。"""
        parsed = urlsplit(value)

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("必须是完整的 HTTP 或 HTTPS 地址")

        return value.rstrip("/")

    @field_validator("literature_proxy_url")
    @classmethod
    def proxy_url_must_be_absolute_http_url(cls, value: str | None) -> str | None:
        """校验显式代理地址，避免把无效路由错误延迟到来源请求阶段。"""
        if value is None:
            return None

        parsed = urlsplit(value)

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("LITERATURE_PROXY_URL 必须是完整的 HTTP 或 HTTPS 地址")

        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> LiteratureSourceSettings:
        """检查分页、路由和访问通道，阻止不完整配置进入 Provider。"""
        if self.openalex_page_size > self.openalex_max_results:
            raise ValueError("OPENALEX_PAGE_SIZE 不能大于 OPENALEX_MAX_RESULTS")

        if self.crossref_page_size > self.crossref_max_results:
            raise ValueError("CROSSREF_PAGE_SIZE 不能大于 CROSSREF_MAX_RESULTS")

        if self.app_env == "production" and self.openalex_enabled and not self.openalex_api_key:
            raise ValueError("生产环境启用 OpenAlex 时必须设置 OPENALEX_API_KEY")

        if self.semantic_scholar_enabled:
            if self.semantic_scholar_access_mode == "ominiai" and not self.s2api_ominiai_api_key:
                raise ValueError(
                    "启用 Ominiai Semantic Scholar 通道时必须设置 S2API_OMINIAI_API_KEY"
                )

            if (
                self.app_env == "production"
                and self.semantic_scholar_access_mode == "official"
                and not self.semantic_scholar_api_key
            ):
                raise ValueError(
                    "生产环境通过官方通道启用 Semantic Scholar 时必须设置 SEMANTIC_SCHOLAR_API_KEY"
                )

        proxy_sources = [
            name
            for name, enabled, override in (
                ("OpenAlex", self.openalex_enabled, self.openalex_network_mode),
                ("Crossref", self.crossref_enabled, self.crossref_network_mode),
                ("arXiv", self.arxiv_enabled, self.arxiv_network_mode),
                (
                    "Semantic Scholar",
                    self.semantic_scholar_enabled,
                    self.semantic_scholar_network_mode,
                ),
                ("DOI Resolver", True, self.doi_resolver_network_mode),
            )
            if enabled and (override or self.literature_default_network_mode) == "proxy"
        ]

        if proxy_sources and self.literature_proxy_url is None:
            source_names = "、".join(proxy_sources)
            raise ValueError(f"{source_names} 使用 proxy 网络路由时必须设置 LITERATURE_PROXY_URL")

        return self

    def _network_settings(self, override: NetworkMode | None) -> ProviderNetworkSettings:
        """将来源覆盖值与全局默认值合并成 Provider 可直接使用的路由配置。"""
        mode = override or self.literature_default_network_mode
        proxy_url = self.literature_proxy_url if mode == "proxy" else None
        return ProviderNetworkSettings(mode=mode, proxy_url=proxy_url)

    @property
    def openalex(self) -> OpenAlexProviderSettings:
        """将扁平环境变量组合为 OpenAlex Provider 可直接消费的配置对象。"""
        return OpenAlexProviderSettings(
            enabled=self.openalex_enabled,
            base_url=self.openalex_base_url,
            api_key=self.openalex_api_key,
            contact_email=self.openalex_contact_email,
            request_timeout_seconds=self.search_http_timeout_seconds,
            page_size=self.openalex_page_size,
            max_results=self.openalex_max_results,
            rate_limit_per_minute=self.openalex_rate_limit_per_minute,
            network=self._network_settings(self.openalex_network_mode),
        )

    @property
    def crossref(self) -> CrossrefProviderSettings:
        """将扁平环境变量组合为 Crossref Provider 可直接消费的配置对象。"""
        return CrossrefProviderSettings(
            enabled=self.crossref_enabled,
            base_url=self.crossref_base_url,
            contact_email=self.crossref_contact_email,
            request_timeout_seconds=self.search_http_timeout_seconds,
            page_size=self.crossref_page_size,
            max_results=self.crossref_max_results,
            rate_limit_per_minute=self.crossref_rate_limit_per_minute,
            network=self._network_settings(self.crossref_network_mode),
        )

    @property
    def arxiv(self) -> ArxivProviderSettings:
        """将扁平环境变量组合为 arXiv Provider 可直接消费的配置对象。"""
        return ArxivProviderSettings(
            enabled=self.arxiv_enabled,
            base_url=self.arxiv_base_url,
            request_timeout_seconds=self.search_http_timeout_seconds,
            max_results=self.arxiv_max_results,
            min_request_interval_seconds=self.arxiv_min_request_interval_seconds,
            contact_email=self.arxiv_contact_email,
            network=self._network_settings(self.arxiv_network_mode),
        )

    @property
    def semantic_scholar(self) -> SemanticScholarProviderSettings:
        """按选定访问通道创建 Semantic Scholar 的 URL、鉴权和网络路由配置。"""
        if self.semantic_scholar_access_mode == "ominiai":
            base_url = self.s2api_ominiai_base_url
            auth_mode: SemanticScholarAuthMode = "bearer"
            auth_token = self.s2api_ominiai_api_key
        else:
            base_url = self.semantic_scholar_base_url
            auth_mode = "x_api_key"
            auth_token = self.semantic_scholar_api_key

        return SemanticScholarProviderSettings(
            enabled=self.semantic_scholar_enabled,
            base_url=base_url,
            access_mode=self.semantic_scholar_access_mode,
            auth_mode=auth_mode,
            auth_token=auth_token,
            request_timeout_seconds=self.search_http_timeout_seconds,
            max_results=self.semantic_scholar_max_results,
            rate_limit_per_minute=self.semantic_scholar_rate_limit_per_minute,
            network=self._network_settings(self.semantic_scholar_network_mode),
        )

    @property
    def doi_resolver(self) -> DoiResolverSettings:
        """将 DOI 题录核验配置暴露给按需调用的解析器。"""
        return DoiResolverSettings(
            base_url=self.doi_resolver_base_url,
            request_timeout_seconds=self.doi_resolver_timeout_seconds,
            network=self._network_settings(self.doi_resolver_network_mode),
        )


@lru_cache
def get_literature_source_settings() -> LiteratureSourceSettings:
    """返回进程内共享的文献源配置，并保证先加载项目根目录 .env。"""
    load_env()
    return LiteratureSourceSettings()
