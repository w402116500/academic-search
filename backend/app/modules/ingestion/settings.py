"""RAG 入库 Worker 的模型、切块和 Milvus 配置。"""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urlsplit

from app.core.env import load_env
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    """将环境变量整理为解析、嵌入和索引任务使用的只读配置。"""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    milvus_uri: str
    milvus_token: SecretStr | None = None
    milvus_collection_name: str = "academic_document_chunks_l3_v1"

    openai_api_key: SecretStr
    openai_base_url: str = "https://api.openai.com/v1"
    openai_embedding_model: str = "text-embedding-3-large"
    rag_embedding_batch_size: int = Field(default=16, ge=1, le=128)

    rag_max_l1_characters: int = Field(default=12_000, ge=512, le=100_000)
    rag_max_l2_characters: int = Field(default=4_000, ge=256, le=50_000)
    rag_max_l3_characters: int = Field(default=1_200, ge=128, le=20_000)
    rag_l3_overlap_characters: int = Field(default=160, ge=0, le=10_000)
    rag_tokenizer_encoding: str = "cl100k_base"

    @field_validator("milvus_uri", "openai_base_url")
    @classmethod
    def endpoint_must_be_absolute_http_url(cls, value: str) -> str:
        """拒绝相对地址和非 HTTP(S) 协议，避免 Worker 连接意外目标。"""
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("服务地址必须是完整的 HTTP 或 HTTPS 地址")
        return value.rstrip("/")

    @field_validator("milvus_collection_name")
    @classmethod
    def collection_name_must_be_safe(cls, value: str) -> str:
        """限制为 Milvus 的普通集合名，避免把配置拼入表达式或路径。"""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,254}", value):
            raise ValueError("MILVUS_COLLECTION_NAME 必须是合法的 Milvus 集合名")
        return value

    @property
    def chunking_snapshot(self) -> dict[str, int | str]:
        """返回可写入入库运行的分块参数，不含任何凭据。"""
        return {
            "max_l1_characters": self.rag_max_l1_characters,
            "max_l2_characters": self.rag_max_l2_characters,
            "max_l3_characters": self.rag_max_l3_characters,
            "l3_overlap_characters": self.rag_l3_overlap_characters,
            "tokenizer_encoding": self.rag_tokenizer_encoding,
        }

    @property
    def embedding_snapshot(self) -> dict[str, str | int]:
        """返回可追溯的 embedding 配置，不保存 API Key。"""
        return {
            "provider": "openai_compatible",
            "model": self.openai_embedding_model,
            "batch_size": self.rag_embedding_batch_size,
        }


@lru_cache
def get_ingestion_settings() -> IngestionSettings:
    """读取项目根目录 .env 后缓存经过校验的 Worker 配置。"""
    load_env()
    return IngestionSettings()  # pyright: ignore[reportCallIssue]
