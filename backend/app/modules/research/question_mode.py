"""Resolve user-facing research question modes into execution paths."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from app.modules.research.contracts import ResearchQuestionMode

RESEARCH_QUESTION_MODE_CONFIG_KEY = "research_question_mode"


class ResearchExecutionMode(StrEnum):
    """Worker-side execution path; kept separate from persisted ResearchRun.mode."""

    FAST_RAG = "fast_rag"
    STRICT_RESEARCH = "strict_research"


@dataclass(frozen=True, slots=True)
class ResearchModeDecision:
    """Deterministic routing result written into retrieval_trace."""

    requested_mode: ResearchQuestionMode
    execution_mode: ResearchExecutionMode
    source: Literal["user", "auto"]
    reason: str
    matched_intents: tuple[str, ...] = ()

    def to_trace(self) -> dict[str, object]:
        """Return the public trace payload for the local mode router."""
        return {
            "classifier": "local_fast_rag_router",
            "requested_mode": self.requested_mode.value,
            "mode": self.execution_mode.value,
            "source": self.source,
            "reason": self.reason,
            **({"matched_intents": list(self.matched_intents)} if self.matched_intents else {}),
        }


_STRICT_INTENT_MARKERS = (
    "比较",
    "对比",
    "差异",
    "区别",
    "优缺点",
    "利弊",
    "综述",
    "系统综述",
    "meta分析",
    "元分析",
    "多篇",
    "多项研究",
    "跨论文",
    "综合",
    "冲突",
    "矛盾",
    "是否一致",
    "一致性",
    "逐条核验",
    "逐项核验",
    "严格核验",
    "深度研究",
    "多个维度",
    "分别",
    "compare",
    "contrast",
    "difference",
    "differences",
    "pros and cons",
    "tradeoff",
    "trade-off",
    "review",
    "survey",
    "synthesize",
    "synthesis",
    "across papers",
    "multiple papers",
    "conflict",
    "contradict",
    "consistency",
    "verify each",
    "strict verification",
)


def research_question_mode_from_config(config: Mapping[str, Any]) -> ResearchQuestionMode:
    """Read a persisted question-mode preference, defaulting old runs to Fast RAG."""
    raw_mode = config.get(RESEARCH_QUESTION_MODE_CONFIG_KEY)
    if isinstance(raw_mode, ResearchQuestionMode):
        return raw_mode
    if isinstance(raw_mode, str):
        try:
            return ResearchQuestionMode(raw_mode)
        except ValueError:
            return ResearchQuestionMode.FAST
    return ResearchQuestionMode.FAST


def model_config_with_question_mode(
    config: Mapping[str, Any], mode: ResearchQuestionMode
) -> dict[str, Any]:
    """Persist the user-facing mode beside the model snapshot without changing DB mode."""
    return {**dict(config), RESEARCH_QUESTION_MODE_CONFIG_KEY: mode.value}


def resolve_research_execution_mode(
    question: str, requested_mode: ResearchQuestionMode
) -> ResearchModeDecision:
    """Map a request preference to the concrete RAG execution path."""
    if requested_mode is ResearchQuestionMode.STRICT:
        return ResearchModeDecision(
            requested_mode=requested_mode,
            execution_mode=ResearchExecutionMode.STRICT_RESEARCH,
            source="user",
            reason="用户选择深度研究，使用完整路由、逐 claim 核验与必要修复链路。",
        )
    if requested_mode is ResearchQuestionMode.FAST:
        return ResearchModeDecision(
            requested_mode=requested_mode,
            execution_mode=ResearchExecutionMode.FAST_RAG,
            source="user",
            reason="用户选择快速问答，跳过逐 claim LLM 核验以优先返回可追溯引用回答。",
        )

    matched_intents = strict_research_intents(question)
    if matched_intents:
        return ResearchModeDecision(
            requested_mode=requested_mode,
            execution_mode=ResearchExecutionMode.STRICT_RESEARCH,
            source="auto",
            reason=f"命中强复杂意图：{'、'.join(matched_intents[:3])}。",
            matched_intents=matched_intents,
        )
    return ResearchModeDecision(
        requested_mode=requested_mode,
        execution_mode=ResearchExecutionMode.FAST_RAG,
        source="auto",
        reason="未命中强复杂意图，灰区问题默认使用快速问答。",
    )


def strict_research_intents(question: str) -> tuple[str, ...]:
    """Return strong complexity markers that justify Strict Research in auto mode."""
    normalized = question.casefold()
    return tuple(marker for marker in _STRICT_INTENT_MARKERS if marker.casefold() in normalized)
