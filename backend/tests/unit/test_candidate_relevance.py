"""候选相关性 Agent 的结构与证据验证测试。"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from app.modules.search.contracts import RawCandidate, SourceName, TriageDecision, UnifiedCandidate
from app.modules.workflow.candidate_relevance import (
    CandidateRelevanceContext,
    OpenAICompatibleCandidateRelevanceEvaluator,
)
from app.modules.workflow.settings import WorkflowSettings
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr


class FakeModel:
    """提供可控结构化结果，避免单元测试调用外部聊天模型。"""

    def __init__(self, response: object) -> None:
        self._response = response

    async def ainvoke(self, input: list[SystemMessage | HumanMessage]) -> object:
        _ = input
        return self._response


def _candidate(
    abstract: str | None = "The study examines sleep quality and academic performance.",
) -> UnifiedCandidate:
    """创建经过统一规整与初筛的最小候选。"""
    source = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id="W1",
        title="Sleep quality and academic performance",
        abstract=abstract,
    )
    return UnifiedCandidate(
        candidate_id=uuid4(),
        title=source.title,
        title_key="sleep quality academic performance",
        abstract=abstract,
        source_records=(source,),
        triage=TriageDecision(included=True),
    )


def _context() -> CandidateRelevanceContext:
    """模拟已经由用户确认过的研究方向。"""
    return CandidateRelevanceContext(
        research_question="睡眠质量是否影响大学生学业表现？",
        direction_title="睡眠质量与学业表现",
        direction_summary="分析两者关联。",
        subtopics=("睡眠质量", "学业表现"),
        search_queries=("sleep quality academic performance",),
        start_year=2020,
        end_year=2026,
        languages=("zh", "en"),
    )


def test_payload_limits_each_candidate_abstract_length() -> None:
    """超长摘要只能占用预先约定的 Agent 输入预算。"""
    candidate = _candidate("0123456789abcdef")

    payload = OpenAICompatibleCandidateRelevanceEvaluator._build_payload(
        _context(),
        (candidate,),
        abstract_max_characters=10,
    )

    serialized = json.loads(payload)
    assert serialized["candidates"][0]["abstract"] == "0123456789"


@pytest.mark.asyncio
async def test_model_evidence_must_be_found_in_unified_candidate() -> None:
    """模型引用不存在的原文时，候选必须明确失败而不是展示伪理由。"""
    candidate = _candidate()
    evaluator = OpenAICompatibleCandidateRelevanceEvaluator(
        WorkflowSettings(deepseek_api_key=SecretStr("test")),
        model=FakeModel(
            {
                "assessments": [
                    {
                        "candidate_id": str(candidate.candidate_id),
                        "level": "core",
                        "study_focus": "考察睡眠质量与学业表现的关系。",
                        "reason": "该研究直接相关。",
                        "helpful_aspect": "帮助分析变量关系。",
                        "limitations": [],
                        "recommendation": "优先获取全文。",
                        "evidence": [{"source_field": "abstract", "quote": "模型虚构的全文结论"}],
                    }
                ]
            }
        ),
    )

    result = await evaluator.assess(context=_context(), candidates=(candidate,))

    assert result[0].relevance_state == "failed"
    assert result[0].relevance_assessment is None


@pytest.mark.asyncio
async def test_invalid_item_does_not_discard_another_verified_candidate() -> None:
    """同一批次的一条坏证据只能让自身失败，不能抹掉其他已核验结果。"""
    first = _candidate()
    second = _candidate("The study examines sleep quality and student wellbeing.")
    evaluator = OpenAICompatibleCandidateRelevanceEvaluator(
        WorkflowSettings(deepseek_api_key=SecretStr("test")),
        model=FakeModel(
            {
                "assessments": [
                    {
                        "candidate_id": str(first.candidate_id),
                        "level": "core",
                        "study_focus": "考察睡眠质量与学业表现之间的关系。",
                        "reason": "研究对象和核心关系与当前方向一致。",
                        "helpful_aspect": "可用于分析睡眠与学业表现的关联。",
                        "limitations": [],
                        "recommendation": "建议优先查看全文。",
                        "evidence": [
                            {
                                "source_field": "abstract",
                                "quote": "sleep quality and academic performance",
                            }
                        ],
                    },
                    {
                        "candidate_id": str(second.candidate_id),
                        "level": "related",
                        "study_focus": "考察学生睡眠和心理健康。",
                        "reason": "可补充相关背景。",
                        "helpful_aspect": "帮助理解学生群体的睡眠问题。",
                        "limitations": [],
                        "recommendation": "可按需查看。",
                        "evidence": [{"source_field": "abstract", "quote": "模型编造的内容"}],
                    },
                ]
            }
        ),
    )

    result = await evaluator.assess(context=_context(), candidates=(first, second))

    assert result[0].relevance_state == "completed"
    assert result[0].relevance_assessment is not None
    assert result[0].relevance_assessment.study_focus.startswith("考察睡眠质量")
    assert result[1].relevance_state == "failed"
    assert result[1].relevance_error is not None
    assert result[1].relevance_error.code == "candidate_relevance_output_invalid"


@pytest.mark.asyncio
async def test_candidate_without_abstract_is_explicitly_insufficient() -> None:
    """缺摘要时不让模型猜测，直接返回信息不足状态。"""
    candidate = _candidate(abstract=None)
    evaluator = OpenAICompatibleCandidateRelevanceEvaluator(
        WorkflowSettings(deepseek_api_key=SecretStr("test")), model=FakeModel({"assessments": []})
    )

    result = await evaluator.assess(context=_context(), candidates=(candidate,))

    assert result[0].relevance_state == "completed"
    assert result[0].relevance_assessment is not None
    assert result[0].relevance_assessment.level == "insufficient_information"
    assert result[0].relevance_assessment.study_focus.startswith("目前只能从题目确认")
