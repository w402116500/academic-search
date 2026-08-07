"""研究问题请求模式的本地分流规则测试。"""

from app.modules.research.contracts import AskResearchQuestionRequest, ResearchQuestionMode
from app.modules.research.question_mode import (
    RESEARCH_QUESTION_MODE_CONFIG_KEY,
    ResearchExecutionMode,
    model_config_with_question_mode,
    research_question_mode_from_config,
    resolve_research_execution_mode,
)


def test_question_request_defaults_to_fast_mode() -> None:
    """旧客户端未传 mode 时，API 契约默认快速问答。"""
    request = AskResearchQuestionRequest.model_validate({"content": "  总结这篇论文的主要发现  "})

    assert request.content == "总结这篇论文的主要发现"
    assert request.mode is ResearchQuestionMode.FAST


def test_explicit_modes_resolve_without_model_router() -> None:
    """用户显式选择优先级最高，不依赖 LLM router。"""
    fast = resolve_research_execution_mode("请解释这个实验结果", ResearchQuestionMode.FAST)
    strict = resolve_research_execution_mode("请解释这个实验结果", ResearchQuestionMode.STRICT)

    assert fast.execution_mode is ResearchExecutionMode.FAST_RAG
    assert fast.source == "user"
    assert strict.execution_mode is ResearchExecutionMode.STRICT_RESEARCH
    assert strict.source == "user"


def test_auto_mode_only_strong_complex_intent_uses_strict_research() -> None:
    """灰区默认 fast；强比较/综合/冲突问题才自动升档。"""
    gray = resolve_research_execution_mode("为什么这个方法有效？", ResearchQuestionMode.AUTO)
    complex_question = resolve_research_execution_mode(
        "请比较多篇论文的结论是否一致，并逐条核验证据冲突。",
        ResearchQuestionMode.AUTO,
    )

    assert gray.execution_mode is ResearchExecutionMode.FAST_RAG
    assert gray.source == "auto"
    assert complex_question.execution_mode is ResearchExecutionMode.STRICT_RESEARCH
    assert complex_question.source == "auto"
    assert {"比较", "多篇", "冲突", "逐条核验"}.issubset(set(complex_question.matched_intents))


def test_question_mode_round_trips_through_model_config() -> None:
    """请求模式写在 model_config 中，避免扩展数据库 run.mode 约束。"""
    config = model_config_with_question_mode({"model": "fake"}, ResearchQuestionMode.STRICT)

    assert config == {"model": "fake", RESEARCH_QUESTION_MODE_CONFIG_KEY: "strict"}
    assert research_question_mode_from_config(config) is ResearchQuestionMode.STRICT
    assert research_question_mode_from_config({"model": "old-client"}) is ResearchQuestionMode.FAST
    assert (
        research_question_mode_from_config({RESEARCH_QUESTION_MODE_CONFIG_KEY: "unexpected"})
        is ResearchQuestionMode.FAST
    )
