"""OpenAI 兼容意图分析适配器的离线结构化输出测试。"""

from __future__ import annotations

import pytest
from app.modules.workflow.intent_analysis import (
    IntentAnalysisError,
    IntentAnalysisErrorCode,
    OpenAICompatibleIntentAnalyzer,
)
from app.modules.workflow.settings import WorkflowSettings
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr


class FakeStructuredModel:
    """按预设值返回结构化输出或异常的 LangChain 调用替身。"""

    def __init__(self, outcome: object) -> None:
        self._outcome = outcome

    async def ainvoke(self, input: list[SystemMessage | HumanMessage]) -> object:
        _ = input
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _settings() -> WorkflowSettings:
    """构造不读取本地 `.env` 的测试模型配置。"""
    return WorkflowSettings(
        workflow_chat_provider="openai_compatible",
        openai_api_key=SecretStr("test-key"),
        openai_base_url="https://example.test/v1",
        openai_chat_model="test-chat-model",
    )


def _valid_draft() -> dict[str, object]:
    """返回满足每个方向均有独立查询计划的最小模型输出。"""
    return {
        "direction_options": [
            {
                "id": "built-environment",
                "title": "建成环境与心理健康",
                "summary": "研究公共空间特征与居民心理健康之间的关联。",
                "subtopics": ["绿地可达性"],
            },
            {
                "id": "social-cohesion",
                "title": "社区凝聚力与心理健康",
                "summary": "考察公共空间通过社会互动影响心理健康的机制。",
                "subtopics": ["社会互动"],
            },
        ],
        "suggested_scope": {"start_year": 2018, "end_year": 2025, "languages": ["en"]},
        "direction_query_plans": [
            {
                "direction_id": "built-environment",
                "queries": [
                    {
                        "provider": "openalex",
                        "query": "public space built environment mental health",
                    }
                ],
            },
            {
                "direction_id": "social-cohesion",
                "queries": [
                    {
                        "provider": "openalex",
                        "query": "public space social cohesion mental health",
                    }
                ],
            },
        ],
    }


@pytest.mark.asyncio
async def test_analyzer_returns_validated_plan_and_server_owned_model_snapshot() -> None:
    """模型只提供研究内容；调用模型、地址和提示词版本由服务端写入快照。"""
    analyzer = OpenAICompatibleIntentAnalyzer(
        _settings(),
        model=FakeStructuredModel(_valid_draft()),
    )

    result = await analyzer.analyze("公共空间如何影响城市居民的心理健康？")

    assert len(result.draft.direction_options) == 2
    assert set(result.draft.direction_query_plans[0].model_dump()) == {"direction_id", "queries"}
    assert result.model_snapshot["model"] == "test-chat-model"
    assert "api_key" not in result.model_snapshot


def test_deepseek_configuration_is_the_default_chat_provider() -> None:
    """默认聊天后端使用 DeepSeek，且审计快照不保存密钥。"""
    settings = WorkflowSettings(
        deepseek_api_key=SecretStr("test-deepseek-key"),
        deepseek_base_url="https://api.deepseek.com/v1",
        deepseek_chat_model="deepseek-chat",
    )

    assert settings.active_chat_model == "deepseek-chat"
    assert settings.model_snapshot["provider"] == "deepseek"
    assert settings.model_snapshot["base_url"] == "https://api.deepseek.com/v1"


@pytest.mark.asyncio
async def test_analyzer_rejects_invalid_json_structure() -> None:
    """少方向、少范围或少查询计划的返回都不能进入确认页面。"""
    analyzer = OpenAICompatibleIntentAnalyzer(
        _settings(),
        model=FakeStructuredModel({"direction_options": []}),
    )

    with pytest.raises(IntentAnalysisError) as error:
        await analyzer.analyze("测试研究要求")

    assert error.value.code is IntentAnalysisErrorCode.OUTPUT_INVALID


@pytest.mark.asyncio
async def test_analyzer_marks_gateway_failure_without_empty_plan_fallback() -> None:
    """网关异常必须明确失败，不能伪造空计划让后续检索继续。"""
    analyzer = OpenAICompatibleIntentAnalyzer(
        _settings(),
        model=FakeStructuredModel(TimeoutError("gateway timed out")),
    )

    with pytest.raises(IntentAnalysisError) as error:
        await analyzer.analyze("测试研究要求")

    assert error.value.code is IntentAnalysisErrorCode.MODEL_REQUEST_FAILED
