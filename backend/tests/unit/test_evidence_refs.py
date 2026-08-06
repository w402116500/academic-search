"""EvidenceRef utility behavior that does not require graph execution."""

from __future__ import annotations

import pytest
from app.modules.agents.evidence_refs import (
    CitationFragmentationAssessment,
    assess_citation_fragmentation,
)


def test_citation_fragmentation_gate_triggers_on_three_adjacent_same_single_ref() -> None:
    """Three adjacent citation-bearing sentences using one same ref trigger the gate."""
    assessment = assess_citation_fragmentation(
        "第一项结论成立【E1】。第二项结论也成立[E1]！第三项结论仍由同一证据支持【E1】？"
    )

    assert assessment == CitationFragmentationAssessment(
        triggered=True,
        citation_bearing_sentence_count=3,
        max_same_ref_sentence_run=3,
        repeated_ref="E1",
    )
    assert assessment.to_trace() == {
        "triggered": True,
        "citation_bearing_sentence_count": 3,
        "max_same_ref_sentence_run": 3,
        "repeated_ref": "E1",
    }


@pytest.mark.parametrize(
    ("answer", "citation_sentence_count", "max_run"),
    [
        ("第一项结论成立【E1】。第二项结论也成立【E1】。", 2, 2),
        ("第一、第二和第三项结论在同一语义段中说明【E1】。", 1, 1),
        ("第一项结论成立【E1】。第二项结论成立【E2】。第三项结论仍成立【E1】。", 3, 1),
        (
            "第一项结论成立【E1】。中间解释不带引用。第二项结论成立【E1】。第三项结论成立【E1】。",
            3,
            2,
        ),
        (
            "第一项结论成立【E1】。第二项同时依赖两条证据【E1】【E2】。"
            "第三项结论成立【E1】。第四项结论成立【E1】。",
            4,
            2,
        ),
    ],
)
def test_citation_fragmentation_gate_skips_normal_or_mixed_patterns(
    answer: str,
    citation_sentence_count: int,
    max_run: int,
) -> None:
    """Grouped, short, varied-ref, uncited-break, and multi-ref patterns do not trigger."""
    assessment = assess_citation_fragmentation(answer)

    assert not assessment.triggered
    assert assessment.citation_bearing_sentence_count == citation_sentence_count
    assert assessment.max_same_ref_sentence_run == max_run
    assert assessment.repeated_ref is None


def test_citation_fragmentation_gate_does_not_split_decimal_text() -> None:
    """Decimal points such as p<0.001 are not sentence boundaries for this gate."""
    assessment = assess_citation_fragmentation(
        "第一项 p<0.001【E1】第二项 p<0.001【E1】第三项 p<0.001【E1】。"
    )

    assert not assessment.triggered
    assert assessment.citation_bearing_sentence_count == 1
    assert assessment.max_same_ref_sentence_run == 1
    assert assessment.repeated_ref is None
