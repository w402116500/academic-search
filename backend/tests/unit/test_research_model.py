"""OpenAI 兼容研究模型的正文引用恢复行为。"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import pytest
from app.infra.llm.research_model import OpenAICompatibleResearchModel
from app.modules.agents.contracts import AnswerClaimDraft, AnswerDraft
from app.modules.rag.retrieval import RetrievedEvidence
from pydantic import BaseModel

_CHUNK_ID = UUID("00000000-0000-0000-0000-000000000801")
_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000802")
_INGESTION_RUN_ID = UUID("00000000-0000-0000-0000-000000000803")
_PAPER_ID = UUID("00000000-0000-0000-0000-000000000804")


class _FakeStructuredModel:
    def __init__(self, response: AnswerDraft) -> None:
        self._response = response

    async def ainvoke(self, _messages: object) -> AnswerDraft:
        return self._response


class _FakeChatClient:
    def __init__(self, response: AnswerDraft) -> None:
        self._response = response

    def with_structured_output(
        self,
        schema: type[BaseModel],
        *,
        method: str,
    ) -> _FakeStructuredModel:
        assert schema is AnswerDraft
        assert method == "json_mode"
        return _FakeStructuredModel(self._response)


def _model(response: AnswerDraft) -> OpenAICompatibleResearchModel:
    model = object.__new__(OpenAICompatibleResearchModel)
    model._client = cast(Any, _FakeChatClient(response))
    return model


def _evidence() -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id=_CHUNK_ID,
        document_id=_DOCUMENT_ID,
        ingestion_run_id=_INGESTION_RUN_ID,
        paper_id=_PAPER_ID,
        content="The reported result supports the answer.",
        page_start=1,
        page_end=1,
        section_path=("Results",),
        locator={},
        title="Citation Recovery Test Paper",
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


@pytest.mark.asyncio
async def test_generate_answer_recovers_missing_prose_citations_from_complete_claims() -> None:
    """Writer 正文遗漏标记但主张完整时，适配器应返回已通过 canonical 校验的草稿。"""
    draft = AnswerDraft(
        answer="第一项结论成立。第二项结论也成立。",
        cited_refs=["E1"],
        claims=[
            AnswerClaimDraft(claim_id="C1", text="第一项结论成立", refs=["E1"]),
            AnswerClaimDraft(claim_id="C2", text="第二项结论也成立", refs=["E1"]),
        ],
        evidence_sufficient=True,
    )

    result = await _model(draft).generate_answer(question="结论是什么？", evidences=(_evidence(),))

    assert result.answer == "第一项结论成立。第二项结论也成立。【E1】"
    assert result.cited_refs == ["E1"]
