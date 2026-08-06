"""Fallback behavior for optional RAG presentation editing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast
from uuid import UUID

import pytest
from app.modules.agents import presentation
from app.modules.agents.contracts import (
    AnswerClaimVerification,
    AnswerClaimVerificationItem,
    PresentationAnswerDraft,
    ResearchBudgetExhausted,
    ResearchChatModel,
    ResearchModelError,
)
from app.modules.agents.presentation import conditionally_edit_verified_answer
from app.modules.rag.retrieval import RetrievedEvidence

_CHUNK_ID = UUID("00000000-0000-0000-0000-000000000901")
_SECOND_CHUNK_ID = UUID("00000000-0000-0000-0000-000000000905")
_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000902")
_INGESTION_RUN_ID = UUID("00000000-0000-0000-0000-000000000903")
_PAPER_ID = UUID("00000000-0000-0000-0000-000000000904")
_WRITER_ANSWER = "研究结论有效【E1】。研究结论稳定【E1】。研究结论可复现【E1】。"


def _evidence() -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id=_CHUNK_ID,
        document_id=_DOCUMENT_ID,
        ingestion_run_id=_INGESTION_RUN_ID,
        paper_id=_PAPER_ID,
        content="Evidence text is intentionally unavailable to the presentation editor.",
        page_start=1,
        page_end=1,
        section_path=("Results",),
        locator={},
        title="Presentation Test Paper",
        authors=(),
        publication_year=2024,
        source_url=None,
        vector_score=None,
        lexical_score=None,
        rrf_score=0.1,
        rerank_score=None,
        rank=1,
        source_chunk_ids=(_CHUNK_ID,),
    )


def _second_evidence() -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id=_SECOND_CHUNK_ID,
        document_id=_DOCUMENT_ID,
        ingestion_run_id=_INGESTION_RUN_ID,
        paper_id=_PAPER_ID,
        content="A second snapshot item that the editor is not authorized to cite.",
        page_start=2,
        page_end=2,
        section_path=("Results",),
        locator={},
        title="Presentation Test Paper",
        authors=(),
        publication_year=2024,
        source_url=None,
        vector_score=None,
        lexical_score=None,
        rrf_score=0.09,
        rerank_score=None,
        rank=2,
        source_chunk_ids=(_SECOND_CHUNK_ID,),
    )


def _supported_verification() -> AnswerClaimVerification:
    return AnswerClaimVerification(
        claims=[
            AnswerClaimVerificationItem(
                claim_id="C1",
                claim="研究结论",
                supported=True,
                supporting_refs=["E1"],
            )
        ]
    )


class PresentationModelStub:
    """Minimal structural fake for the two model calls used by the presentation branch."""

    def __init__(
        self,
        *,
        editor_answer: str = "现有证据支持该研究结论有效、稳定且可复现【E1】。",
        editor_refs: Sequence[str] = ("E1",),
        second_verifier_supported: bool = True,
        editor_error: Exception | None = None,
        editor_delay_seconds: float = 0,
    ) -> None:
        self.editor_answer = editor_answer
        self.editor_refs = tuple(editor_refs)
        self.second_verifier_supported = second_verifier_supported
        self.editor_error = editor_error
        self.editor_delay_seconds = editor_delay_seconds
        self.editor_calls = 0
        self.verifier_calls = 0
        self.supported_claims: list[tuple[str, ...]] = []
        self.allowed_refs: list[tuple[str, ...]] = []

    async def edit_answer_presentation(
        self,
        *,
        question: str,
        supported_claims: Sequence[AnswerClaimVerificationItem],
        allowed_refs: Sequence[str],
    ) -> PresentationAnswerDraft:
        assert question
        self.editor_calls += 1
        self.supported_claims.append(tuple(item.claim for item in supported_claims))
        self.allowed_refs.append(tuple(allowed_refs))
        if self.editor_delay_seconds:
            await asyncio.sleep(self.editor_delay_seconds)
        if self.editor_error is not None:
            raise self.editor_error
        return PresentationAnswerDraft(answer=self.editor_answer, cited_refs=list(self.editor_refs))

    async def verify_answer_claims(
        self,
        *,
        question: str,
        answer: str,
        evidences: Sequence[RetrievedEvidence],
        cited_refs: Sequence[str],
    ) -> AnswerClaimVerification:
        assert question
        assert answer
        assert evidences
        self.verifier_calls += 1
        return AnswerClaimVerification(
            claims=[
                AnswerClaimVerificationItem(
                    claim_id="C1",
                    claim="研究结论",
                    supported=self.second_verifier_supported,
                    supporting_refs=["E1"] if self.second_verifier_supported else [],
                )
            ]
        )


async def _call_model(operation: Callable[[], Awaitable[Any]]) -> Any:
    return await operation()


async def _edit(
    model: PresentationModelStub,
    *,
    call_model: Callable[[Callable[[], Awaitable[Any]]], Awaitable[Any]] = _call_model,
):
    return await conditionally_edit_verified_answer(
        model=cast(ResearchChatModel, model),
        call_model=call_model,
        question="该方法的实验结果是什么？",
        writer_answer=_WRITER_ANSWER,
        verification=_supported_verification(),
        evidences=(_evidence(),),
        cited_refs=("E1",),
    )


@pytest.mark.asyncio
async def test_presentation_editor_protocol_failure_keeps_verified_writer_answer() -> None:
    """Unknown prose refs are optional-editor protocol failures, never user-visible failures."""
    model = PresentationModelStub(
        editor_answer="改写后的研究结论【E2】。",
        editor_refs=("E2",),
    )

    result = await _edit(model)

    assert result.answer == _WRITER_ANSWER
    assert result.cited_refs == ("E1",)
    assert model.editor_calls == 1
    assert model.verifier_calls == 0
    assert result.audit["presentation_edit"] == {
        "status": "fallback",
        "fallback_reason": "protocol_error",
        "editor_answer": "改写后的研究结论【E2】。",
    }


@pytest.mark.asyncio
async def test_presentation_editor_cannot_cite_snapshot_ref_outside_supported_claim_set() -> None:
    """The orchestration boundary enforces the editor's closed ref set independently of adapters."""
    model = PresentationModelStub(
        editor_answer="改写后的研究结论【E2】。",
        editor_refs=("E2",),
    )

    result = await conditionally_edit_verified_answer(
        model=cast(ResearchChatModel, model),
        call_model=_call_model,
        question="该方法的实验结果是什么？",
        writer_answer=_WRITER_ANSWER,
        verification=_supported_verification(),
        evidences=(_evidence(), _second_evidence()),
        cited_refs=("E1",),
    )

    assert result.answer == _WRITER_ANSWER
    assert result.cited_refs == ("E1",)
    assert model.verifier_calls == 0
    assert result.audit["presentation_edit"] == {
        "status": "fallback",
        "fallback_reason": "protocol_error",
        "editor_answer": "改写后的研究结论【E2】。",
    }


@pytest.mark.asyncio
async def test_presentation_editor_rejected_by_second_verifier_keeps_writer_answer() -> None:
    """A rejected edit falls back instead of invoking repair, clarification, or a retry."""
    model = PresentationModelStub(second_verifier_supported=False)

    result = await _edit(model)

    assert result.answer == _WRITER_ANSWER
    assert model.editor_calls == 1
    assert model.verifier_calls == 1
    presentation_edit = result.audit["presentation_edit"]
    assert isinstance(presentation_edit, dict)
    assert presentation_edit["fallback_reason"] == "verifier_rejected"


@pytest.mark.asyncio
async def test_presentation_editor_timeout_has_one_aggregate_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The editor and verifier share one deadline; timeout never retries the editor."""
    monkeypatch.setattr(presentation, "PRESENTATION_EDIT_TIMEOUT_SECONDS", 0.001)
    model = PresentationModelStub(editor_delay_seconds=0.01)

    result = await _edit(model)

    assert result.answer == _WRITER_ANSWER
    assert model.editor_calls == 1
    assert model.verifier_calls == 0
    assert result.audit["presentation_edit"] == {
        "status": "fallback",
        "fallback_reason": "timeout",
    }


@pytest.mark.asyncio
async def test_presentation_editor_model_error_or_call_budget_keeps_writer_answer() -> None:
    """Optional presentation work cannot turn a verified answer into a failed research run."""
    model_error_result = await _edit(
        PresentationModelStub(editor_error=ResearchModelError("provider unavailable"))
    )

    async def exhausted_call_model(
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        del operation
        raise ResearchBudgetExhausted("no model calls remain")

    budget_result = await _edit(PresentationModelStub(), call_model=exhausted_call_model)

    assert model_error_result.answer == _WRITER_ANSWER
    model_error_edit = model_error_result.audit["presentation_edit"]
    assert isinstance(model_error_edit, dict)
    assert model_error_edit["fallback_reason"] == "model_error"
    assert budget_result.answer == _WRITER_ANSWER
    assert budget_result.audit["presentation_edit"] == {
        "status": "fallback",
        "fallback_reason": "model_error",
        "model_call_budget_exhausted": True,
    }
