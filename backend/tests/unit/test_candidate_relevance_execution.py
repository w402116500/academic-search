"""独立相关性 Worker 的字段级快照合并测试。"""

from __future__ import annotations

from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateRelevanceAssessment,
    CandidateRelevanceEvidence,
    CandidateRelevanceLevel,
    CandidateRelevanceState,
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.workflow.candidate_relevance_execution import CandidateRelevanceRunExecutor


def _candidate() -> UnifiedCandidate:
    source = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id="relevance-merge",
        title="Sleep quality and mental health",
        abstract="The study examines sleep quality and mental health outcomes.",
    )
    return UnifiedCandidate(
        doi="10.1000/relevance.merge",
        title=source.title,
        title_key="sleep quality mental health",
        authors=(CandidateAuthor(name="Ada Lovelace"),),
        abstract=source.abstract,
        source_records=(source,),
        triage=TriageDecision(included=True),
        citation=CitationMetadata(
            status=CitationMetadataStatus.READY,
            authors=(CitationAuthor(given="Ada", family="Lovelace"),),
            title=source.title,
            document_type="journal_article",
            issued_date=CitationDate(year=2024),
            doi="10.1000/relevance.merge",
            url="https://doi.org/10.1000/relevance.merge",
        ),
    )


def test_relevance_merge_only_replaces_relevance_fields() -> None:
    """模型完成时不覆盖同时写入的题录、候选原文或准备清单。"""
    candidate = _candidate()
    assessed = candidate.model_copy(
        update={
            "relevance_state": CandidateRelevanceState.COMPLETED,
            "relevance_assessment": CandidateRelevanceAssessment(
                level=CandidateRelevanceLevel.CORE,
                study_focus="研究睡眠质量与心理健康。",
                reason="标题和摘要都直接涉及这两个变量。",
                helpful_aspect="可用于分析两者关系。",
                recommendation="建议优先核验全文。",
                evidence=(
                    CandidateRelevanceEvidence(
                        source_field="title", quote="Sleep quality and mental health"
                    ),
                ),
            ),
        }
    )
    snapshot = {
        "status": "running",
        "stage": "relevance_assessment",
        "candidate_counts": {},
        "candidates": [candidate.model_dump(mode="json")],
        "candidate_selection": {"selected_candidate_ids": [str(candidate.candidate_id)]},
    }

    merged = CandidateRelevanceRunExecutor._merge_relevance(snapshot, (assessed,))
    result = UnifiedCandidate.model_validate(merged["candidates"][0])

    assert result.citation == candidate.citation
    assert result.title == candidate.title
    assert result.source_records == candidate.source_records
    assert result.relevance_state is CandidateRelevanceState.COMPLETED
    assert result.relevance_assessment == assessed.relevance_assessment
    assert merged["candidate_selection"] == snapshot["candidate_selection"]
