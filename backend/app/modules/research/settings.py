"""RAG 检索、证据图和运行事件的类型化配置。"""

from __future__ import annotations

import os
from functools import lru_cache

from app.core.env import load_env
from pydantic import Field
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
    rag_max_query_rewrites: int = Field(default=1, ge=0, le=1)
    rag_max_subquestions: int = Field(default=4, ge=2, le=8)
    rag_max_react_tool_calls: int = Field(default=6, ge=1, le=12)
    rag_max_parallel_subquestions: int = Field(default=2, ge=1, le=4)
    rag_event_ttl_seconds: int = Field(default=86_400, ge=300, le=604_800)
    rag_chat_timeout_seconds: float = Field(default=90, gt=0, le=300)
    rag_checkpoint_database_url: str | None = None

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
