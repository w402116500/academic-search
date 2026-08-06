"""Model-facing evidence refs and user-facing citation rendering."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.modules.agents.contracts import AnswerClaimDraft
from app.modules.rag.retrieval import RetrievedEvidence

EVIDENCE_REF_PATTERN = re.compile(r"^E[1-9][0-9]*$")
EVIDENCE_REF_TOKEN_PATTERN = re.compile(r"【(E[1-9][0-9]*)】|\[(E[1-9][0-9]*)\]")
# 正文已经出现模型侧或用户侧引用外观时拒绝补标，避免把原有标记与重建结果混用。
# E 分支刻意放宽匹配范围，使格式错误或不属于快照的 E-ref 继续交给严格协议校验拒绝。
PRESENT_CITATION_TOKEN_PATTERN = re.compile(
    r"【\s*(?:E[^】]*|[0-9][0-9,;、\-\s]*)】|\[\s*(?:E[^\]]*|[0-9][0-9,;、\-\s]*)\]",
    re.IGNORECASE,
)
CHINESE_SENTENCE_TERMINATORS = frozenset("。！？")
UUID_TEXT_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True, slots=True)
class UserCitation:
    """One final user-facing citation mapped back to a snapshot-local evidence ref."""

    display_index: int
    evidence_ref: str
    chunk_id: UUID


@dataclass(frozen=True, slots=True)
class CitationFragmentationAssessment:
    """Deterministic gate metrics for optional presentation-quality editing."""

    triggered: bool
    citation_bearing_sentence_count: int
    max_same_ref_sentence_run: int
    repeated_ref: str | None = None

    def to_trace(self) -> dict[str, object]:
        """Return the JSON-compatible audit payload stored under presentation_quality."""
        return {
            "triggered": self.triggered,
            "citation_bearing_sentence_count": self.citation_bearing_sentence_count,
            "max_same_ref_sentence_run": self.max_same_ref_sentence_run,
            "repeated_ref": self.repeated_ref,
        }


def evidence_ref_for_index(index: int) -> str:
    """Return the stable model-side ref for a one-based evidence index."""
    if index < 1:
        raise ValueError("EvidenceRef index must be one-based.")
    return f"E{index}"


def evidence_refs_for(evidences: Sequence[RetrievedEvidence]) -> tuple[str, ...]:
    """Return snapshot-local refs in the same order as the model evidence prompt."""
    return tuple(evidence_ref_for_index(index) for index, _ in enumerate(evidences, start=1))


def evidence_ref_map(evidences: Sequence[RetrievedEvidence]) -> dict[str, RetrievedEvidence]:
    """Map model-facing refs to retrieved evidence without exposing UUIDs to models."""
    return dict(zip(evidence_refs_for(evidences), evidences, strict=True))


def evidence_snapshot_trace(evidences: Sequence[RetrievedEvidence]) -> list[dict[str, object]]:
    """Serialize the EvidenceSnapshot mapping into the research run trace."""
    return [
        {
            "evidence_ref": ref,
            "chunk_id": str(evidence.chunk_id),
            "rank": evidence.rank,
            "title": evidence.title,
            "page_start": evidence.page_start,
            "page_end": evidence.page_end,
        }
        for ref, evidence in evidence_ref_map(evidences).items()
    ]


def is_uuid_text(value: str) -> bool:
    """Detect UUID leakage in model-facing evidence-ref fields."""
    return UUID_TEXT_PATTERN.fullmatch(value) is not None


def invalid_evidence_refs(refs: Iterable[str], allowed_refs: set[str]) -> tuple[str, ...]:
    """Return refs that are malformed, UUID-shaped, or absent from the snapshot."""
    invalid: list[str] = []
    for ref in refs:
        if (
            ref not in allowed_refs
            or is_uuid_text(ref)
            or EVIDENCE_REF_PATTERN.fullmatch(ref) is None
        ):
            invalid.append(ref)
    return tuple(invalid)


def resolve_evidence_refs(
    evidences: Sequence[RetrievedEvidence],
    refs: Iterable[str],
) -> tuple[RetrievedEvidence, ...]:
    """Resolve refs to evidence, preserving first-use order and removing duplicates."""
    ref_map = evidence_ref_map(evidences)
    resolved: list[RetrievedEvidence] = []
    seen: set[str] = set()
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        resolved.append(ref_map[ref])
    return tuple(resolved)


def chunk_ids_for_refs(
    evidences: Sequence[RetrievedEvidence], refs: Iterable[str]
) -> tuple[UUID, ...]:
    """Resolve final cited refs to chunk UUIDs for persistence."""
    return tuple(evidence.chunk_id for evidence in resolve_evidence_refs(evidences, refs))


def assess_citation_fragmentation(answer: str) -> CitationFragmentationAssessment:
    """Assess whether adjacent same-ref cited sentences are mechanically fragmented."""
    citation_bearing_sentence_count = 0
    max_same_ref_sentence_run = 0
    max_run_ref: str | None = None
    current_ref: str | None = None
    current_run = 0

    for sentence in _split_chinese_terminal_sentences(answer):
        refs = _unique_evidence_refs_in_text(sentence)
        if refs:
            citation_bearing_sentence_count += 1

        if len(refs) != 1:
            current_ref = None
            current_run = 0
            continue

        sentence_ref = refs[0]
        if sentence_ref == current_ref:
            current_run += 1
        else:
            current_ref = sentence_ref
            current_run = 1

        if current_run > max_same_ref_sentence_run:
            max_same_ref_sentence_run = current_run
            max_run_ref = sentence_ref

    triggered = max_same_ref_sentence_run >= 3
    return CitationFragmentationAssessment(
        triggered=triggered,
        citation_bearing_sentence_count=citation_bearing_sentence_count,
        max_same_ref_sentence_run=max_same_ref_sentence_run,
        repeated_ref=max_run_ref if triggered else None,
    )


def evidence_refs_in_text(answer: str, evidences: Sequence[RetrievedEvidence]) -> tuple[str, ...]:
    """Return final-answer refs by first-use order, validating they belong to the snapshot."""
    ref_map = evidence_ref_map(evidences)
    refs: list[str] = []
    for ref in _unique_evidence_refs_in_text(answer):
        if ref not in ref_map:
            raise ValueError(f"Unknown evidence ref in final answer: {ref}")
        refs.append(ref)
    return tuple(refs)


def validate_answer_cited_refs(
    answer: str,
    evidences: Sequence[RetrievedEvidence],
    cited_refs: Sequence[str],
) -> tuple[str, ...]:
    """Validate that prose citations and structured cited_refs describe the same refs."""
    text_refs = evidence_refs_in_text(answer, evidences)
    if set(text_refs) != set(cited_refs):
        raise ValueError("Answer text refs do not match cited_refs.")
    if not text_refs:
        raise ValueError("Answer text has no evidence refs.")
    return text_refs


def recover_answer_prose_citations(
    answer: str,
    claims: Sequence[AnswerClaimDraft],
    cited_refs: Sequence[str],
) -> str | None:
    """仅在主张能完整且无歧义地逐句映射时恢复缺失的正文引用。"""
    if PRESENT_CITATION_TOKEN_PATTERN.search(answer) is not None or not claims:
        return None

    cited_ref_set = set(cited_refs)
    if not cited_ref_set or any(EVIDENCE_REF_PATTERN.fullmatch(ref) is None for ref in cited_refs):
        return None

    sentence_spans = _nonempty_chinese_sentence_spans(answer)
    if not sentence_spans:
        return None

    sentence_claims: list[list[AnswerClaimDraft]] = [[] for _ in sentence_spans]
    for claim in claims:
        claim_refs = frozenset(claim.refs)
        if not claim_refs or any(EVIDENCE_REF_PATTERN.fullmatch(ref) is None for ref in claim_refs):
            return None
        matching_sentences = [
            index
            for index, (start, end) in enumerate(sentence_spans)
            if claim.text.strip() in _claim_texts_for_sentence(answer[start:end])
        ]
        # 同一主张若对应多个句子，或只能覆盖句子的一部分，均不能安全推断其归属。
        if len(matching_sentences) != 1:
            return None
        sentence_claims[matching_sentences[0]].append(claim)

    claim_ref_union = set().union(*(set(claim.refs) for claim in claims))
    if cited_ref_set != claim_ref_union:
        return None

    ordered_cited_refs = tuple(dict.fromkeys(cited_refs))
    sentence_ref_sets: list[frozenset[str]] = []
    for mapped_claims in sentence_claims:
        if not mapped_claims:
            return None
        ref_sets = {frozenset(claim.refs) for claim in mapped_claims}
        if len(ref_sets) != 1:
            return None
        sentence_ref_sets.append(ref_sets.pop())

    insertion_points: list[tuple[int, frozenset[str]]] = []
    current_refs: frozenset[str] | None = None
    current_run_end = 0
    for (_, end), sentence_refs in zip(sentence_spans, sentence_ref_sets, strict=True):
        if sentence_refs != current_refs:
            if current_refs is not None:
                insertion_points.append((current_run_end, current_refs))
            current_refs = sentence_refs
        current_run_end = end
    if current_refs is not None:
        insertion_points.append((current_run_end, current_refs))

    recovered = answer
    # 从后向前插入，确保位置始终对应尚未改动的原始正文。
    for position, refs in reversed(insertion_points):
        citation_group = "".join(f"【{ref}】" for ref in ordered_cited_refs if ref in refs)
        recovered = f"{recovered[:position]}{citation_group}{recovered[position:]}"
    return recovered


def canonical_answer_cited_refs(
    answer: str,
    evidences: Sequence[RetrievedEvidence],
    cited_refs: Sequence[str],
) -> tuple[str, ...]:
    """Return prose refs in first-use order after strict prose/structure agreement."""
    allowed_refs = set(evidence_ref_map(evidences))
    invalid_refs = invalid_evidence_refs(cited_refs, allowed_refs)
    if invalid_refs:
        raise ValueError("Structured cited_refs contain invalid evidence refs.")
    return validate_answer_cited_refs(answer, evidences, cited_refs)


def render_user_citations(
    answer: str,
    evidences: Sequence[RetrievedEvidence],
) -> tuple[str, tuple[UserCitation, ...]]:
    """Replace model-side refs in final prose with dense user-facing citation numbers."""
    ref_map = evidence_ref_map(evidences)
    ref_to_display: dict[str, int] = {}
    citations: list[UserCitation] = []

    def replace(match: re.Match[str]) -> str:
        ref = match.group(1) or match.group(2)
        if ref not in ref_map:
            raise ValueError(f"Unknown evidence ref in final answer: {ref}")
        display_index = ref_to_display.get(ref)
        if display_index is None:
            display_index = len(ref_to_display) + 1
            ref_to_display[ref] = display_index
            citations.append(
                UserCitation(
                    display_index=display_index,
                    evidence_ref=ref,
                    chunk_id=ref_map[ref].chunk_id,
                )
            )
        return f"[{display_index}]"

    rendered = EVIDENCE_REF_TOKEN_PATTERN.sub(replace, answer)
    return rendered, tuple(citations)


def _split_chinese_terminal_sentences(text: str) -> tuple[str, ...]:
    """Split only on Chinese terminal punctuation, preserving decimal values."""
    sentences: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if char not in CHINESE_SENTENCE_TERMINATORS:
            continue
        sentence = text[start : index + 1].strip()
        if sentence:
            sentences.append(sentence)
        start = index + 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return tuple(sentences)


def _nonempty_chinese_sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    """返回保留原始字符位置的非空中文句子范围，供后续精确插入引用。"""
    spans: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char not in CHINESE_SENTENCE_TERMINATORS:
            continue
        end = index + 1
        if text[start:end].strip():
            spans.append((start, end))
        start = end
    if text[start:].strip():
        spans.append((start, len(text)))
    return tuple(spans)


def _claim_texts_for_sentence(sentence: str) -> frozenset[str]:
    """仅允许 claim 精确覆盖整句，且 claim 可选择省略句末中文标点。"""
    normalized = sentence.strip()
    if not normalized:
        return frozenset()
    candidates = {normalized}
    if normalized[-1] in CHINESE_SENTENCE_TERMINATORS:
        without_terminal = normalized[:-1].rstrip()
        if without_terminal:
            candidates.add(without_terminal)
    return frozenset(candidates)


def _unique_evidence_refs_in_text(text: str) -> tuple[str, ...]:
    """Return distinct E-refs in first-use order without validating a snapshot."""
    refs: list[str] = []
    seen: set[str] = set()
    for match in EVIDENCE_REF_TOKEN_PATTERN.finditer(text):
        ref = match.group(1) or match.group(2)
        if ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
    return tuple(refs)
