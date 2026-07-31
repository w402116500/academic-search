"""研究意图分析使用的 OpenAI 兼容模型配置。"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit

from app.core.env import load_env
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkflowSettings(BaseSettings):
    """意图分析器的最小模型配置，不复用 RAG embedding 的专用参数。"""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    openai_api_key: SecretStr
    openai_base_url: str = "https://api.openai.com/v1"
    openai_chat_model: str = "gpt-4.1-mini"
    workflow_intent_timeout_seconds: float = Field(default=45, gt=0, le=180)

    @field_validator("openai_base_url")
    @classmethod
    def endpoint_must_be_absolute_http_url(cls, value: str) -> str:
        """阻止模型调用使用相对路径或非 HTTP(S) 目标。"""
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OPENAI_BASE_URL 必须是完整的 HTTP 或 HTTPS 地址")
        return value.rstrip("/")

    @field_validator("openai_chat_model")
    @classmethod
    def model_name_cannot_be_blank(cls, value: str) -> str:
        """避免直到 Worker 执行时才发现模型名为空。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("OPENAI_CHAT_MODEL 不能为空白")
        return normalized

    @property
    def model_snapshot(self) -> dict[str, str | float]:
        """保存可追溯模型元信息，明确排除 API Key。"""
        return {
            "provider": "openai_compatible",
            "model": self.openai_chat_model,
            "base_url": self.openai_base_url,
            "timeout_seconds": self.workflow_intent_timeout_seconds,
            "prompt_version": "intent-analysis-v1",
        }


@lru_cache
def get_workflow_settings() -> WorkflowSettings:
    """加载根目录 `.env` 并缓存已校验的意图分析模型配置。"""
    load_env()
    return WorkflowSettings()  # pyright: ignore[reportCallIssue]
