"""真实候选相关性 Agent 验收：结构化理由必须回链到候选公开元数据。"""

from __future__ import annotations

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


def _candidates() -> tuple[UnifiedCandidate, ...]:
    """构造同一检索集合中的多条候选，不向外部来源或本地存储写入测试数据。"""
    records = (
        RawCandidate(
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
        ),
        RawCandidate(
            source=SourceName.SEMANTIC_SCHOLAR,
            source_record_id="live-relevance-street-trees",
            title="Street tree canopy and depressive symptoms among urban adults",
            abstract=(
                "This longitudinal cohort study estimates associations between residential street "
                "tree canopy, depressive symptoms, and socioeconomic conditions among urban adults."
            ),
            published_year=2023,
            document_type="article",
            language=CandidateLanguage.ENGLISH,
        ),
    )
    return tuple(
        UnifiedCandidate(
            candidate_id=uuid4(),
            title=record.title,
            title_key=" ".join(record.title.casefold().split()),
            abstract=record.abstract,
            published_year=record.published_year,
            document_type=record.document_type,
            language=record.language or CandidateLanguage.UNKNOWN,
            source_records=(record,),
            triage=TriageDecision(included=True),
        )
        for record in records
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_candidate_relevance_assessment_never_exposes_unverified_claims() -> None:
    """真实模型只能展示通过二次核验的理由，扩大解释必须安全降级。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实相关性验收")

    candidates = _candidates()
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
        candidates=candidates,
    )

    assert len(result) == len(candidates)
    assert {candidate.candidate_id for candidate in result} == {
        candidate.candidate_id for candidate in candidates
    }
    completed_count = 0
    rejected_count = 0
    for candidate in result:
        if candidate.relevance_state == "failed":
            rejected_count += 1
            assert candidate.relevance_assessment is None
            assert candidate.relevance_error is not None
            assert candidate.relevance_error.code.startswith("candidate_relevance_claim_")
            continue

        completed_count += 1
        assert candidate.relevance_state == "completed"
        assert candidate.relevance_assessment is not None
        assert candidate.relevance_assessment.study_focus
        assert candidate.relevance_assessment.reason
        assert candidate.relevance_assessment.helpful_aspect
        assert candidate.relevance_assessment.recommendation
        assert candidate.relevance_assessment.evidence
        for evidence in candidate.relevance_assessment.evidence:
            source = candidate.title if evidence.source_field == "title" else candidate.abstract
            assert source is not None
            assert " ".join(evidence.quote.casefold().split()) in " ".join(
                source.casefold().split()
            )

    assert completed_count + rejected_count == len(candidates)
    print(
        "live relevance claim-verification acceptance passed: "
        f"{completed_count} completed, {rejected_count} rejected"
    )
