"""Bounded presentation editing for already-supported RAG answers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from app.modules.agents.contracts import (
    AnswerClaimVerification,
    PresentationAnswerDraft,
    ResearchBudgetExhausted,
    ResearchChatModel,
    ResearchModelError,
    ResearchModelProtocolError,
)
from app.modules.agents.evidence_refs import (
    assess_citation_fragmentation,
    canonical_answer_cited_refs,
    invalid_evidence_refs,
)
from app.modules.rag.retrieval import RetrievedEvidence

PRESENTATION_EDIT_TIMEOUT_SECONDS = 45.0
ModelCaller = Callable[[Callable[[], Awaitable[Any]]], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class PresentationEditResult:
    """Selected answer plus server-only audit information for one optional edit."""

    answer: str
    cited_refs: tuple[str, ...]
    audit: dict[str, object]


async def conditionally_edit_verified_answer(
    *,
    model: ResearchChatModel,
    call_model: ModelCaller,
    question: str,
    writer_answer: str,
    verification: AnswerClaimVerification,
    evidences: Sequence[RetrievedEvidence],
    cited_refs: Sequence[str],
) -> PresentationEditResult:
    """Edit only a fragmented, already-supported answer and otherwise keep it unchanged."""
    assessment = assess_citation_fragmentation(writer_answer)
    audit_base: dict[str, object] = {
        "citation_fragmentation": assessment.to_trace(),
        "writer_answer": writer_answer,
    }
    original_refs = tuple(cited_refs)
    if not assessment.triggered:
        return PresentationEditResult(
            answer=writer_answer,
            cited_refs=original_refs,
            audit={**audit_base, "presentation_edit": {"status": "skipped"}},
        )

    supported_claims = tuple(item for item in verification.claims if item.supported)
    if len(supported_claims) != len(verification.claims):
        raise ResearchModelProtocolError("展示编辑只能在全部回答主张已支持后执行。")
    supported_refs = tuple(
        dict.fromkeys(ref for item in supported_claims for ref in item.supporting_refs)
    )
    editor_answer: str | None = None
    try:
        async with asyncio.timeout(PRESENTATION_EDIT_TIMEOUT_SECONDS):
            edited = cast(
                PresentationAnswerDraft,
                await call_model(
                    lambda: model.edit_answer_presentation(
                        question=question,
                        supported_claims=supported_claims,
                        allowed_refs=supported_refs,
                    )
                ),
            )
            editor_answer = edited.answer
            try:
                edited_refs = canonical_answer_cited_refs(
                    edited.answer,
                    evidences,
                    edited.cited_refs,
                )
            except ValueError as exc:
                raise ResearchModelProtocolError("展示编辑器正文引用无效。") from exc
            if invalid_evidence_refs(edited_refs, set(supported_refs)):
                raise ResearchModelProtocolError("展示编辑器使用了未获支持主张授权的证据。")
            second_verification = cast(
                AnswerClaimVerification,
                await call_model(
                    lambda: model.verify_answer_claims(
                        question=question,
                        answer=edited.answer,
                        evidences=evidences,
                        cited_refs=edited_refs,
                    )
                ),
            )
            _ensure_supporting_refs_are_cited(second_verification, edited_refs)
            if any(not item.supported for item in second_verification.claims):
                return _fallback_result(
                    writer_answer=writer_answer,
                    original_refs=original_refs,
                    audit_base=audit_base,
                    reason="verifier_rejected",
                    editor_answer=editor_answer,
                )
            return PresentationEditResult(
                answer=edited.answer,
                cited_refs=edited_refs,
                audit={
                    **audit_base,
                    "presentation_edit": {
                        "status": "applied",
                        "editor_answer": editor_answer,
                    },
                },
            )
    except TimeoutError:
        return _fallback_result(
            writer_answer=writer_answer,
            original_refs=original_refs,
            audit_base=audit_base,
            reason="timeout",
            editor_answer=editor_answer,
        )
    except ResearchModelProtocolError:
        return _fallback_result(
            writer_answer=writer_answer,
            original_refs=original_refs,
            audit_base=audit_base,
            reason="protocol_error",
            editor_answer=editor_answer,
        )
    except ResearchBudgetExhausted:
        return _fallback_result(
            writer_answer=writer_answer,
            original_refs=original_refs,
            audit_base=audit_base,
            reason="model_error",
            editor_answer=editor_answer,
            model_call_budget_exhausted=True,
        )
    except ResearchModelError:
        return _fallback_result(
            writer_answer=writer_answer,
            original_refs=original_refs,
            audit_base=audit_base,
            reason="model_error",
            editor_answer=editor_answer,
        )


def _fallback_result(
    *,
    writer_answer: str,
    original_refs: tuple[str, ...],
    audit_base: dict[str, object],
    reason: str,
    editor_answer: str | None,
    model_call_budget_exhausted: bool = False,
) -> PresentationEditResult:
    """Keep a verified Writer answer visible when optional editing cannot complete."""
    presentation_edit: dict[str, object] = {
        "status": "fallback",
        "fallback_reason": reason,
    }
    if editor_answer is not None:
        presentation_edit["editor_answer"] = editor_answer
    if model_call_budget_exhausted:
        presentation_edit["model_call_budget_exhausted"] = True
    return PresentationEditResult(
        answer=writer_answer,
        cited_refs=original_refs,
        audit={**audit_base, "presentation_edit": presentation_edit},
    )


def _ensure_supporting_refs_are_cited(
    verification: AnswerClaimVerification, cited_refs: Sequence[str]
) -> None:
    """Prevent the second verifier from using evidence absent from the edited prose."""
    allowed_refs = set(cited_refs)
    for item in verification.claims:
        invalid = invalid_evidence_refs(item.supporting_refs, allowed_refs)
        if invalid:
            raise ResearchModelProtocolError("展示编辑后的核验器引用了正文未使用的证据。")
