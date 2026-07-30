"""全文获取与私有对象存储的类型化配置。"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit

from app.core.env import load_env
from app.core.settings import NetworkMode
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FulltextAcquisitionSettings(BaseSettings):
    """自动获取开放获取 PDF 所需的网络、体积和 S3 配置。"""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    fulltext_download_timeout_seconds: float = Field(default=30, gt=0, le=120)
    fulltext_total_timeout_seconds: float = Field(default=90, gt=0, le=600)
    fulltext_max_file_size_bytes: int = Field(default=52_428_800, ge=1_024, le=524_288_000)
    fulltext_max_redirects: int = Field(default=3, ge=0, le=10)
    fulltext_staging_prefix: str = "staging/fulltext"
    fulltext_network_mode: NetworkMode = "direct"
    literature_proxy_url: str | None = None

    s3_endpoint_url: str
    s3_region: str = Field(min_length=1, max_length=128)
    s3_bucket: str = Field(min_length=3, max_length=255)
    s3_access_key: SecretStr
    s3_secret_key: SecretStr
    s3_force_path_style: bool = True

    @field_validator("s3_endpoint_url")
    @classmethod
    def s3_endpoint_must_be_absolute_http_url(cls, value: str) -> str:
        """拒绝非 HTTP(S) endpoint，避免对象存储客户端访问意外协议。"""
        parsed = urlsplit(value)

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("S3_ENDPOINT_URL 必须是完整的 HTTP 或 HTTPS 地址")

        return value.rstrip("/")

    @field_validator("fulltext_staging_prefix")
    @classmethod
    def staging_prefix_must_be_safe(cls, value: str) -> str:
        """对象键前缀不能逃逸到 bucket 的其他业务目录。"""
        normalized = value.strip().strip("/")

        if not normalized or ".." in normalized.split("/"):
            raise ValueError("FULLTEXT_STAGING_PREFIX 必须是非空且不含 .. 的对象键前缀")

        return normalized

    @field_validator("literature_proxy_url")
    @classmethod
    def proxy_url_must_be_absolute_http_url(cls, value: str | None) -> str | None:
        """复用文献源代理地址前，先限制为明确的 HTTP(S) 代理端点。"""
        if value is None:
            return None

        parsed = urlsplit(value)

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("LITERATURE_PROXY_URL 必须是完整的 HTTP 或 HTTPS 地址")

        return value.rstrip("/")

    @model_validator(mode="after")
    def proxy_mode_requires_proxy_url(self) -> FulltextAcquisitionSettings:
        """全文下载仅在显式代理模式下读取代理地址，避免隐式继承全局代理。"""
        if self.fulltext_network_mode == "proxy" and self.literature_proxy_url is None:
            raise ValueError("FULLTEXT_NETWORK_MODE=proxy 时必须设置 LITERATURE_PROXY_URL")

        return self

    @property
    def download_proxy_url(self) -> str | None:
        """向 HTTP 客户端暴露当前全文下载唯一允许使用的代理地址。"""
        return self.literature_proxy_url if self.fulltext_network_mode == "proxy" else None


@lru_cache
def get_fulltext_acquisition_settings() -> FulltextAcquisitionSettings:
    """读取一次根目录 .env，并复用已校验的全文获取配置。"""
    load_env()
    # S3 必填项由 load_env() 读取的部署环境提供，静态检查无法推断这些运行时值。
    return FulltextAcquisitionSettings()  # pyright: ignore[reportCallIssue]
