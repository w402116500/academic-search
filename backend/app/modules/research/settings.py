"""RAG 检索、证据图和运行事件的类型化配置。"""

from __future__ import annotations

import os
from functools import lru_cache
from urllib.parse import urlsplit

from app.core.env import load_env
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ResearchSettings(BaseSettings):
    """只保存研究运行的非敏感行为参数，模型凭据继续复用既有配置。"""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    rag_vector_candidate_limit: int = Field(default=24, ge=1, le=100)
    rag_keyword_candidate_limit: int = Field(default=24, ge=1, le=100)
    rag_final_evidence_limit: int = Field(default=6, ge=1, le=20)
    rag_rrf_k: int = Field(default=60, ge=1, le=500)
    rag_min_rrf_score: float = Field(default=0.010, ge=0, le=1)
    rag_parent_merge_min_hits: int = Field(default=2, ge=2, le=10)
    rag_reranker_url: str | None = None
    rag_reranker_api_key: SecretStr | None = None
    rag_reranker_model: str | None = Field(default=None, min_length=1, max_length=256)
    rag_reranker_candidate_limit: int = Field(default=24, ge=1, le=100)
    rag_reranker_document_max_characters: int = Field(default=6_000, ge=200, le=20_000)
    rag_reranker_timeout_seconds: float = Field(default=30, gt=0, le=120)
    rag_max_query_rewrites: int = Field(default=1, ge=0, le=1)
    rag_max_subquestions: int = Field(default=4, ge=2, le=8)
    rag_max_react_tool_calls: int = Field(default=6, ge=1, le=12)
    rag_max_parallel_subquestions: int = Field(default=2, ge=1, le=4)
    rag_max_model_calls_per_run: int = Field(default=16, ge=2, le=40)
    rag_user_daily_research_run_limit: int = Field(default=20, ge=1, le=1_000)
    rag_global_daily_research_run_limit: int = Field(default=200, ge=1, le=10_000)
    rag_event_ttl_seconds: int = Field(default=86_400, ge=300, le=604_800)
    rag_chat_timeout_seconds: float = Field(default=90, gt=0, le=300)
    rag_checkpoint_database_url: str | None = None

    @field_validator(
        "rag_reranker_url",
        "rag_reranker_api_key",
        "rag_reranker_model",
        mode="before",
    )
    @classmethod
    def empty_reranker_configuration_is_none(cls, value: object) -> object:
        """允许 `.env` 保留待填写的空 Reranker 项，而不误判为半配置。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("rag_reranker_url")
    @classmethod
    def reranker_url_must_be_absolute_http_url(cls, value: str | None) -> str | None:
        """只允许显式的 HTTP(S) 重排服务端点，避免把任意值交给网络客户端。"""
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("RAG_RERANKER_URL 必须是完整的 HTTP 或 HTTPS 地址")
        return value.rstrip("/")

    @model_validator(mode="after")
    def reranker_configuration_is_all_or_nothing(self) -> ResearchSettings:
        """部分配置不能被误认成启用的真实重排器。"""
        configured = (
            self.rag_reranker_url,
            self.rag_reranker_api_key,
            self.rag_reranker_model,
        )
        if any(value is not None for value in configured) and not all(
            value is not None for value in configured
        ):
            raise ValueError(
                "RAG_RERANKER_URL、RAG_RERANKER_API_KEY 和 RAG_RERANKER_MODEL 必须同时设置"
            )
        return self

    @property
    def reranker_enabled(self) -> bool:
        """只有完整配置后才允许检索路径调用外部重排服务。"""
        return self.rag_reranker_url is not None

    @property
    def checkpoint_database_url(self) -> str:
        """返回 psycopg 可用的 PostgreSQL URL，不将 asyncpg 驱动名传给 checkpointer。"""
        raw_url = self.rag_checkpoint_database_url or os.getenv("DATABASE_URL", "")
        if raw_url.startswith("postgresql+asyncpg://"):
            return "postgresql://" + raw_url.removeprefix("postgresql+asyncpg://")
        if raw_url.startswith("postgresql://"):
            return raw_url
        raise ValueError("RAG_CHECKPOINT_DATABASE_URL 或 DATABASE_URL 必须是 PostgreSQL 地址")


@lru_cache
def get_research_settings() -> ResearchSettings:
    """读取根目录 `.env` 后缓存已校验的研究运行配置。"""
    load_env()
    return ResearchSettings()
