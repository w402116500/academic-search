"""候选相关性运行级重试和取消快照转换测试。"""

from __future__ import annotations

from uuid import uuid4

from app.modules.search.contracts import (
    CandidateRelevanceError,
    CandidateRelevanceLevel,
    CandidateRelevanceState,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.workflow.candidate_relevance_service import CandidateRelevanceService


def _candidate(
    *,
    abstract: str | None,
    included: bool = True,
    failed: bool = True,
) -> UnifiedCandidate:
    source = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id=str(uuid4()),
        title="Sleep quality and mental health",
        abstract=abstract,
    )
    return UnifiedCandidate(
        candidate_id=uuid4(),
        title=source.title,
        title_key="sleep quality mental health",
        abstract=abstract,
        source_records=(source,),
        triage=TriageDecision(included=included),
        relevance_state=CandidateRelevanceState.FAILED
        if failed
        else CandidateRelevanceState.PENDING,
        relevance_error=(
            CandidateRelevanceError(
                code="candidate_relevance_model_unavailable",
                message="模型暂时不可用。",
                retryable=True,
            )
            if failed
            else None
        ),
    )


def test_run_retry_resets_the_complete_eligible_collection_without_requerying() -> None:
    """整批重试会重置每篇有摘要候选，不把失败范围收窄为单项。"""
    with_abstract = _candidate(abstract="The study examines sleep and anxiety.")
    without_abstract = _candidate(abstract=None)
    excluded = _candidate(abstract="Excluded metadata.", included=False)
    snapshot = {
        "status": "partial_failed",
        "stage": "completed",
        "candidate_counts": {},
        "candidates": [
            with_abstract.model_dump(mode="json"),
            without_abstract.model_dump(mode="json"),
            excluded.model_dump(mode="json"),
        ],
    }

    reset = CandidateRelevanceService._reset_relevance_snapshot(snapshot)
    candidates = CandidateRelevanceService._deserialize_candidates(reset)

    assert reset["status"] == "running"
    assert reset["stage"] == "relevance_assessment"
    assert candidates[0].relevance_state is CandidateRelevanceState.PENDING
    assert candidates[1].relevance_state is CandidateRelevanceState.COMPLETED
    assert candidates[1].relevance_assessment is not None
    assert (
        candidates[1].relevance_assessment.level is CandidateRelevanceLevel.INSUFFICIENT_INFORMATION
    )
    assert candidates[2].relevance_state is CandidateRelevanceState.SKIPPED
    assert reset["candidate_counts"]["relevance_pending_count"] == 1


def test_cancel_marks_only_unfinished_candidates_as_retryable_failures() -> None:
    """取消不会伪造已完成结果，但让待处理项可在后续整批重跑。"""
    pending = _candidate(abstract="The study examines sleep and anxiety.", failed=False)
    snapshot = {
        "status": "running",
        "stage": "relevance_assessment",
        "candidate_counts": {},
        "candidates": [pending.model_dump(mode="json")],
    }

    cancelled = CandidateRelevanceService._cancel_relevance_snapshot(snapshot)
    candidate = CandidateRelevanceService._deserialize_candidates(cancelled)[0]

    assert cancelled["status"] == "cancelled"
    assert candidate.relevance_state is CandidateRelevanceState.FAILED
    assert candidate.relevance_error is not None
    assert candidate.relevance_error.code == "candidate_relevance_cancelled"
    assert candidate.relevance_error.retryable is True
