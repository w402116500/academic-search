"""研究对话模型的配置。

DeepSeek 和 OpenAI 都提供 OpenAI 兼容接口，因此调用层仍复用 LangChain 的
``ChatOpenAI``；通过 ``WORKFLOW_CHAT_PROVIDER`` 选择实际的聊天模型，避免把
聊天模型和 RAG embedding 模型的凭据混在一起。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from app.core.env import load_env
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

WorkflowChatProvider = Literal["deepseek", "openai_compatible"]


class WorkflowSettings(BaseSettings):
    """意图分析器和后续研究对话使用的最小聊天模型配置。"""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # 面向研究对话的默认模型是 DeepSeek；OpenAI 兼容配置仍可作为备用后端。
    workflow_chat_provider: WorkflowChatProvider = "deepseek"
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-chat"

    # OPENAI_* 主要供 embedding 使用；当 provider 显式设为 openai_compatible 时也可用于聊天。
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_chat_model: str = "gpt-4.1-mini"
    workflow_intent_timeout_seconds: float = Field(default=45, gt=0, le=180)

    @field_validator("deepseek_api_key", "openai_api_key", mode="before")
    @classmethod
    def empty_api_keys_are_none(cls, value: object) -> object:
        """把空环境变量转换为 None，避免空凭据被误认为已配置。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("deepseek_base_url", "openai_base_url")
    @classmethod
    def endpoint_must_be_absolute_http_url(cls, value: str) -> str:
        """阻止模型调用使用相对路径或非 HTTP(S) 目标。"""
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("聊天模型 Base URL 必须是完整的 HTTP 或 HTTPS 地址")
        return value.rstrip("/")

    @field_validator("deepseek_chat_model", "openai_chat_model")
    @classmethod
    def model_name_cannot_be_blank(cls, value: str) -> str:
        """避免直到 Worker 执行时才发现模型名为空。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("聊天模型名称不能为空白")
        return normalized

    @model_validator(mode="after")
    def active_provider_must_be_configured(self) -> WorkflowSettings:
        """启动 Worker 前校验当前选择的聊天后端确实有可用凭据。"""
        if self.active_api_key is None:
            env_name = (
                "DEEPSEEK_API_KEY"
                if self.workflow_chat_provider == "deepseek"
                else "OPENAI_API_KEY"
            )
            raise ValueError(f"当前聊天模型需要设置 {env_name}")
        return self

    @property
    def active_api_key(self) -> SecretStr | None:
        """返回当前聊天后端的密钥，调用方不需要分支读取环境变量。"""
        if self.workflow_chat_provider == "deepseek":
            return self.deepseek_api_key
        return self.openai_api_key

    @property
    def active_base_url(self) -> str:
        """返回当前聊天后端的 OpenAI 兼容地址。"""
        if self.workflow_chat_provider == "deepseek":
            return self.deepseek_base_url
        return self.openai_base_url

    @property
    def active_chat_model(self) -> str:
        """返回当前聊天后端的模型标识。"""
        if self.workflow_chat_provider == "deepseek":
            return self.deepseek_chat_model
        return self.openai_chat_model

    @property
    def model_snapshot(self) -> dict[str, str | float]:
        """保存可追溯模型元信息，明确排除 API Key。"""
        return {
            "provider": self.workflow_chat_provider,
            "model": self.active_chat_model,
            "base_url": self.active_base_url,
            "timeout_seconds": self.workflow_intent_timeout_seconds,
            "prompt_version": "intent-analysis-v1",
        }


@lru_cache
def get_workflow_settings() -> WorkflowSettings:
    """加载根目录 `.env` 并缓存已校验的意图分析模型配置。"""
    load_env()
    return WorkflowSettings()  # pyright: ignore[reportCallIssue]
