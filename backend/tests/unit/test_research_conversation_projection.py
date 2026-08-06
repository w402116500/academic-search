"""Research conversation API projection tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.infra.db.models.research import ResearchRun
from app.infra.db.repositories.research_conversations import (
    SqlAlchemyResearchConversationAdapter,
    _public_retrieval_trace,
)
from app.modules.research.contracts import ResearchRunStage, ResearchRunStatus

_CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000000a01")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000a02")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000a03")
_INPUT_MESSAGE_ID = UUID("00000000-0000-0000-0000-000000000a04")


def _completed_run(retrieval_trace: dict[str, Any]) -> ResearchRun:
    now = datetime.now(UTC)
    return ResearchRun(
        id=_RUN_ID,
        conversation_id=_CONVERSATION_ID,
        collection_id=_COLLECTION_ID,
        input_message_id=_INPUT_MESSAGE_ID,
        mode="single_rag",
        status=ResearchRunStatus.COMPLETED.value,
        stage=ResearchRunStage.COMPLETED.value,
        model_config={"model": "fake-research-model"},
        retrieval_trace=retrieval_trace,
        started_at=now,
        stage_started_at=now,
        finished_at=now,
        created_at=now,
    )


def test_public_retrieval_trace_removes_server_only_diagnostics() -> None:
    trace = {
        "stage": "completed",
        "rewrite_attempts": 0,
        "governance": {"model_calls_used": 3},
        "presentation_quality": {
            "writer_answer": "raw writer answer",
            "presentation_edit": {"editor_answer": "raw editor answer"},
        },
        "failure_diagnostics": {
            "failure_code": "research_model_protocol_failed",
            "model_output_summary": "structured_output_rejected",
            "evidence_snapshot": [{"evidence_ref": "E1", "chunk_id": "raw chunk id"}],
        },
    }

    public_trace = _public_retrieval_trace(trace)
    response = SqlAlchemyResearchConversationAdapter._run_response(_completed_run(trace))

    expected_public_trace = {
        "stage": "completed",
        "rewrite_attempts": 0,
        "governance": {"model_calls_used": 3},
    }
    assert public_trace == expected_public_trace
    assert response.retrieval_trace == expected_public_trace
    assert trace["presentation_quality"]["writer_answer"] == "raw writer answer"
    assert trace["failure_diagnostics"]["evidence_snapshot"] == [
        {"evidence_ref": "E1", "chunk_id": "raw chunk id"}
    ]
