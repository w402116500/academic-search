"""真实候选相关性 Agent 验收：结构化理由必须回链到候选公开元数据。"""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from app.modules.search.contracts import (
    CandidateLanguage,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.workflow.candidate_relevance import (
    CandidateRelevanceContext,
    OpenAICompatibleCandidateRelevanceEvaluator,
)
from app.modules.workflow.settings import get_workflow_settings

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_CANDIDATE_RELEVANCE_TESTS"


def _live_test_is_enabled() -> bool:
    """真实模型调用必须由环境变量明确开启，避免日常测试产生费用。"""
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


def _candidate() -> UnifiedCandidate:
    """构造与来源规整后形态一致的候选，不向外部来源或本地存储写入测试数据。"""
    source = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id="live-relevance-urban-green-space",
        title="Urban green space and mental health",
        abstract=(
            "This review examines associations between urban green space exposure and mental "
            "health outcomes. It discusses accessibility, frequency of use, and differences "
            "across population groups."
        ),
        published_year=2024,
        document_type="review",
        language=CandidateLanguage.ENGLISH,
    )
    return UnifiedCandidate(
        candidate_id=uuid4(),
        title=source.title,
        title_key="urban green space mental health",
        abstract=source.abstract,
        published_year=2024,
        document_type=source.document_type,
        language=CandidateLanguage.ENGLISH,
        source_records=(source,),
        triage=TriageDecision(included=True),
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_candidate_relevance_assessment_is_grounded_in_candidate_metadata() -> None:
    """DeepSeek 的展示理由应完整、可读，并且每条依据都能回到标题或摘要。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实相关性验收")

    candidate = _candidate()
    context = CandidateRelevanceContext(
        research_question="城市绿地如何影响居民心理健康？",
        direction_title="城市绿地暴露与心理健康结果",
        direction_summary="关注绿地接触、可达性和不同人群的心理健康结果。",
        subtopics=("绿地暴露", "心理健康", "人群差异"),
        search_queries=("urban green space mental health",),
        start_year=2020,
        end_year=2026,
        languages=("en",),
    )

    result = await OpenAICompatibleCandidateRelevanceEvaluator(get_workflow_settings()).assess(
        context=context,
        candidates=(candidate,),
    )

    assessed = result[0]
    assert assessed.relevance_state == "completed"
    assert assessed.relevance_assessment is not None
    assert assessed.relevance_assessment.study_focus
    assert assessed.relevance_assessment.reason
    assert assessed.relevance_assessment.helpful_aspect
    assert assessed.relevance_assessment.recommendation
    assert assessed.relevance_assessment.evidence

    for evidence in assessed.relevance_assessment.evidence:
        source = candidate.title if evidence.source_field == "title" else candidate.abstract
        assert source is not None
        assert " ".join(evidence.quote.casefold().split()) in " ".join(source.casefold().split())

    print(
        json.dumps(
            {
                "candidate_id": str(candidate.candidate_id),
                "relevance_state": assessed.relevance_state,
                "assessment": assessed.relevance_assessment.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
