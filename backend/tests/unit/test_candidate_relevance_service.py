"""候选相关性快照统计测试。"""

from __future__ import annotations

from uuid import uuid4

from app.modules.search.contracts import (
    CandidateRelevanceAssessment,
    CandidateRelevanceEvidence,
    CandidateRelevanceLevel,
    CandidateRelevanceState,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.search.relevance import exclude_candidate_relevance
from app.modules.search.relevance_execution import CandidateRelevanceRunExecutor


def _candidate(level: CandidateRelevanceLevel) -> UnifiedCandidate:
    source = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id=str(uuid4()),
        title="Sleep quality and mental health",
        abstract="The study examines sleep quality and mental health.",
    )
    return UnifiedCandidate(
        candidate_id=uuid4(),
        title=source.title,
        title_key="sleep quality mental health",
        abstract=source.abstract,
        source_records=(source,),
        triage=TriageDecision(included=True),
        relevance_state=(
            CandidateRelevanceState.COMPLETED
            if level
            in {
                CandidateRelevanceLevel.CORE,
                CandidateRelevanceLevel.RELATED,
                CandidateRelevanceLevel.BACKGROUND,
            }
            else CandidateRelevanceState.EXCLUDED
        ),
        relevance_assessment=CandidateRelevanceAssessment(
            level=level,
            study_focus="测试候选。",
            reason="测试相关性层级。",
            helpful_aspect="测试。",
            recommendation="测试。",
            evidence=(CandidateRelevanceEvidence(source_field="title", quote=source.title),),
        ),
    )


def test_snapshot_counts_publish_positive_screening_and_exclusion_totals() -> None:
    """用户只接收总数、处理数、排除数和实际可筛选数。"""
    core = _candidate(CandidateRelevanceLevel.CORE)
    background = _candidate(CandidateRelevanceLevel.BACKGROUND)
    not_recommended = _candidate(CandidateRelevanceLevel.NOT_RECOMMENDED)
    unsupported = exclude_candidate_relevance(
        _candidate(CandidateRelevanceLevel.CORE),
        "理由不能由标题和摘要支持。",
        code="candidate_relevance_claim_unsupported",
    )
    snapshot = {"candidate_counts": {"candidate_count": 4}}

    counts = CandidateRelevanceRunExecutor._candidate_counts(
        snapshot,
        (core, background, not_recommended, unsupported),
    )

    assert counts["relevance_total_count"] == 4
    assert counts["relevance_analyzed_count"] == 4
    assert counts["relevance_excluded_count"] == 2
    assert counts["screening_candidate_count"] == 2
    assert "relevance_failed_count" not in counts
    assert "relevance_insufficient_count" not in counts
