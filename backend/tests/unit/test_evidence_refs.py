"""EvidenceRef utility behavior that does not require graph execution."""

from __future__ import annotations

import pytest
from app.modules.agents.contracts import AnswerClaimDraft
from app.modules.agents.evidence_refs import (
    CitationFragmentationAssessment,
    assess_citation_fragmentation,
    recover_answer_prose_citations,
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


def test_recover_answer_prose_citations_preserves_text_and_groups_same_ref_sentences() -> None:
    """完整的单来源主张映射只在连续同源句组末尾补一个标记。"""
    answer = "第一项结论成立。 \n第二项结论也成立！\n第三项结论来自另一片段？"
    claims = [
        AnswerClaimDraft(claim_id="C1", text="第一项结论成立", refs=["E1"]),
        AnswerClaimDraft(claim_id="C2", text="第二项结论也成立", refs=["E1"]),
        AnswerClaimDraft(claim_id="C3", text="第三项结论来自另一片段", refs=["E2"]),
    ]

    recovered = recover_answer_prose_citations(answer, claims, ["E1", "E2"])

    assert (
        recovered == "第一项结论成立。 \n第二项结论也成立！【E1】\n第三项结论来自另一片段？【E2】"
    )


def test_recover_answer_prose_citations_groups_multiple_refs_in_structured_order() -> None:
    """同一多来源集合的连续句组在末尾获得一组稳定顺序的引用。"""
    answer = "第一项结论成立。第二项结论也成立。"
    claims = [
        AnswerClaimDraft(claim_id="C1", text="第一项结论成立", refs=["E2", "E1"]),
        AnswerClaimDraft(claim_id="C2", text="第二项结论也成立", refs=["E1", "E2"]),
    ]

    recovered = recover_answer_prose_citations(answer, claims, ["E1", "E2"])

    assert recovered == "第一项结论成立。第二项结论也成立。【E1】【E2】"


@pytest.mark.parametrize(
    "answer",
    [
        "结论已经带有正文引用【E1】。",
        "结论已经带有未知引用【E9】。",
        "结论已经带有用户编号[1]。",
        "结论已经带有全角用户编号【1】。",
        "结论已经带有多个用户编号[1, 2]。",
    ],
)
def test_recover_answer_prose_citations_refuses_any_existing_citation_syntax(answer: str) -> None:
    """已有有效、未知或用户侧标记时，必须继续走严格校验而非自动补标。"""
    claims = [AnswerClaimDraft(claim_id="C1", text="结论已经带有正文引用", refs=["E1"])]

    assert recover_answer_prose_citations(answer, claims, ["E1"]) is None


@pytest.mark.parametrize(
    ("answer", "claims", "cited_refs"),
    [
        (
            "结论带有必要限定。",
            [AnswerClaimDraft(claim_id="C1", text="结论", refs=["E1"])],
            ["E1"],
        ),
        (
            "重复结论。重复结论。",
            [AnswerClaimDraft(claim_id="C1", text="重复结论", refs=["E1"])],
            ["E1"],
        ),
        (
            "同一结论。",
            [
                AnswerClaimDraft(claim_id="C1", text="同一结论", refs=["E1"]),
                AnswerClaimDraft(claim_id="C2", text="同一结论", refs=["E2"]),
            ],
            ["E1", "E2"],
        ),
        (
            "结论成立。",
            [AnswerClaimDraft(claim_id="C1", text="结论成立", refs=["E1"])],
            ["E1", "E2"],
        ),
        (
            "第一项结论成立。第二项结论也成立。",
            [
                AnswerClaimDraft(
                    claim_id="C1",
                    text="第一项结论成立。第二项结论也成立",
                    refs=["E1"],
                )
            ],
            ["E1"],
        ),
        (
            "结论成立。",
            [AnswerClaimDraft(claim_id="C1", text="结论成立", refs=[])],
            ["E1"],
        ),
    ],
)
def test_recover_answer_prose_citations_refuses_ambiguous_or_lossy_mappings(
    answer: str,
    claims: list[AnswerClaimDraft],
    cited_refs: list[str],
) -> None:
    """部分、重复、冲突、跨句、空引用或集合不一致的映射均不得恢复。"""
    assert recover_answer_prose_citations(answer, claims, cited_refs) is None
