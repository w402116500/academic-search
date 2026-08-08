"""研究运行取消、配额和阶段计时的离线服务契约测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from app.infra.db.models.research import Conversation, ResearchRun
from app.infra.db.repositories.research_conversations import (
    SqlAlchemyResearchConversationAdapter,
)
from app.infra.db.repositories.research_execution import (
    SqlAlchemyResearchExecutionAdapter,
)
from app.modules.rag.retrieval import RetrievedEvidence
from app.modules.research.contracts import (
    ResearchError,
    ResearchErrorCode,
    ResearchRunStage,
    ResearchRunStatus,
)
from app.modules.research.settings import ResearchSettings
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000901")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000902")
_CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000000903")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000904")
_INPUT_MESSAGE_ID = UUID("00000000-0000-0000-0000-000000000905")
_CHUNK_ID = UUID("00000000-0000-0000-0000-000000000906")


class FakeExecutionSession:
    """只实现研究运行服务使用到的异步会话表面。"""

    def __init__(
        self,
        scalar_values: list[object | None],
        *,
        scalars_values: list[list[object]] | None = None,
    ) -> None:
        self._scalar_values = iter(scalar_values)
        self._scalars_values = iter(scalars_values or [])
        self.added: list[object] = []
        self.executed: list[object] = []
        self.scalar_statements: list[object] = []
        self.scalars_statements: list[object] = []
        self.commit_count = 0

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeExecutionSession]:
        yield self

    async def scalar(self, _statement: object) -> object | None:
        self.scalar_statements.append(_statement)
        return next(self._scalar_values)

    async def scalars(self, _statement: object) -> list[object]:
        self.scalars_statements.append(_statement)
        return next(self._scalars_values)

    async def execute(self, statement: object) -> object:
        self.executed.append(statement)
        return object()

    def add(self, value: object) -> None:
        self.added.append(value)

    def add_all(self, values: list[object]) -> None:
        self.added.extend(values)

    async def commit(self) -> None:
        self.commit_count += 1


@dataclass(frozen=True, slots=True)
class FakeOutcome:
    """不含证据时的最小图输出，用于验证终态持久化顺序。"""

    status: ResearchRunStatus = ResearchRunStatus.COMPLETED
    stage: ResearchRunStage = ResearchRunStage.COMPLETED
    answer: str = "原文证据支持该回答。"
    evidences: tuple[RetrievedEvidence, ...] = ()
    cited_chunk_ids: tuple[UUID, ...] = ()
    retrieval_trace: dict[str, Any] = field(default_factory=lambda: {"stage": "answering"})
    mode: str = "single_rag"


class RunAccessService(SqlAlchemyResearchConversationAdapter):
    """让取消服务测试聚焦状态迁移，不依赖 SQL 查询细节。"""

    def __init__(self, session: AsyncSession, run: ResearchRun) -> None:
        super().__init__(session)
        self._run = run

    async def _require_owned_run(self, **_: object) -> ResearchRun:
        return self._run


def _run(
    *,
    stage: ResearchRunStage = ResearchRunStage.ANSWERING,
    status: ResearchRunStatus = ResearchRunStatus.RUNNING,
) -> ResearchRun:
    """构造已领取、正在一个公开阶段运行的记录。"""
    now = datetime.now(UTC)
    return ResearchRun(
        id=_RUN_ID,
        conversation_id=_CONVERSATION_ID,
        collection_id=_COLLECTION_ID,
        input_message_id=_INPUT_MESSAGE_ID,
        mode="single_rag",
        status=status.value,
        stage=stage.value,
        model_config={},
        retrieval_trace={
            "stage": stage.value,
            "timing": {
                "started_at": (now - timedelta(seconds=5)).isoformat(),
                "current_stage": stage.value,
                "stage_started_at": (now - timedelta(seconds=2)).isoformat(),
                "stages": [],
            },
        },
        started_at=now - timedelta(seconds=5),
        stage_started_at=now - timedelta(seconds=2),
        created_at=now - timedelta(seconds=5),
    )


def _recorded_stage(run: ResearchRun) -> str:
    timing = cast(dict[str, Any], run.retrieval_trace["timing"])
    stages = cast(list[dict[str, Any]], timing["stages"])
    return cast(str, stages[-1]["stage"])


def _sql(statement: Any) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def _evidence() -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id=_CHUNK_ID,
        document_id=UUID("00000000-0000-0000-0000-000000000907"),
        ingestion_run_id=UUID("00000000-0000-0000-0000-000000000908"),
        paper_id=UUID("00000000-0000-0000-0000-000000000909"),
        content="evidence",
        page_start=None,
        page_end=None,
        section_path=(),
        locator={},
        title="Evidence",
        authors=(),
        publication_year=None,
        source_url=None,
    )


@pytest.mark.asyncio
async def test_complete_closes_the_active_stage_before_persisting_completed() -> None:
    """完成态不能把原本的 answering 阶段篡改为 completed。"""
    run = _run(stage=ResearchRunStage.ANSWERING)
    conversation = Conversation(
        id=_CONVERSATION_ID,
        collection_id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        status="active",
    )
    session = FakeExecutionSession([run, conversation])

    status = await SqlAlchemyResearchExecutionAdapter(cast(AsyncSession, session)).complete(
        _RUN_ID, FakeOutcome()
    )

    assert status is ResearchRunStatus.COMPLETED
    assert run.status == ResearchRunStatus.COMPLETED.value
    assert run.stage == ResearchRunStage.COMPLETED.value
    assert _recorded_stage(run) == ResearchRunStage.ANSWERING.value


@pytest.mark.asyncio
async def test_fail_closes_the_active_stage_before_persisting_failed() -> None:
    """失败态也要保留导致失败的实际阶段，便于后续故障归因。"""
    run = _run(stage=ResearchRunStage.RERANKING)
    session = FakeExecutionSession([run])

    status = await SqlAlchemyResearchExecutionAdapter(cast(AsyncSession, session)).fail(
        _RUN_ID,
        code="research_model_protocol_failed",
        message="模型响应无效",
        diagnostics={
            "model_output_summary": "structured_output_rejected",
            "evidence_snapshot": [{"evidence_ref": "E1", "chunk_id": "chunk-1"}],
        },
    )

    assert status is ResearchRunStatus.FAILED
    assert run.stage == ResearchRunStage.FAILED.value
    assert _recorded_stage(run) == ResearchRunStage.RERANKING.value
    assert run.retrieval_trace["failure_diagnostics"] == {
        "failure_code": "research_model_protocol_failed",
        "model_output_summary": "structured_output_rejected",
        "evidence_snapshot": [{"evidence_ref": "E1", "chunk_id": "chunk-1"}],
    }


@pytest.mark.asyncio
async def test_finalize_cancellation_closes_the_active_stage_before_cancelling() -> None:
    """Worker 在安全边界确认停止后只关闭原阶段，不写回答或证据。"""
    run = _run(stage=ResearchRunStage.HYBRID_RETRIEVAL)
    run.cancel_requested_at = datetime.now(UTC)
    session = FakeExecutionSession([run])

    cancelled = await SqlAlchemyResearchExecutionAdapter(
        cast(AsyncSession, session)
    ).finalize_cancellation(_RUN_ID)

    assert cancelled is True
    assert run.status == ResearchRunStatus.CANCELLED.value
    assert run.stage == ResearchRunStage.CANCELLED.value
    assert _recorded_stage(run) == ResearchRunStage.HYBRID_RETRIEVAL.value
    assert session.added == []


@pytest.mark.asyncio
async def test_finalize_requested_cancellations_recovers_interrupted_worker_run() -> None:
    """Worker 重启时应回收已请求取消、但上一进程未确认终态的运行。"""
    run = _run(stage=ResearchRunStage.PREPARING)
    run.cancel_requested_at = datetime.now(UTC)
    session = FakeExecutionSession([], scalars_values=[[run]])

    run_ids = await SqlAlchemyResearchExecutionAdapter(
        cast(AsyncSession, session)
    ).finalize_requested_cancellations()

    assert run_ids == (_RUN_ID,)
    assert run.status == ResearchRunStatus.CANCELLED.value
    assert run.stage == ResearchRunStage.CANCELLED.value
    assert run.finished_at is not None
    assert run.stage_started_at is None
    assert run.retrieval_trace["cancellation"]["state"] == "confirmed"
    assert _recorded_stage(run) == ResearchRunStage.PREPARING.value
    assert session.added == []


@pytest.mark.asyncio
async def test_running_cancel_records_a_cooperative_stop_request() -> None:
    """运行中取消应保留 running，直到 Worker 在安全边界确认终态。"""
    run = _run(stage=ResearchRunStage.EVIDENCE_VERIFYING)
    session = FakeExecutionSession([])
    service = RunAccessService(cast(AsyncSession, session), run)

    response = await service.cancel_run(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        conversation_id=_CONVERSATION_ID,
        research_run_id=_RUN_ID,
    )

    assert response.status is ResearchRunStatus.RUNNING
    assert response.cancel_requested_at is not None
    assert run.retrieval_trace["cancellation"]["state"] == "requested"
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_submission_quota_rejects_user_and_global_limit_exhaustion() -> None:
    """每日用户额度和全局预算均在创建运行前被稳定拒绝。"""
    settings = ResearchSettings(
        rag_user_daily_research_run_limit=2,
        rag_global_daily_research_run_limit=3,
    )
    user_limited = SqlAlchemyResearchConversationAdapter(
        cast(AsyncSession, FakeExecutionSession([2])), settings=settings
    )
    global_limited = SqlAlchemyResearchConversationAdapter(
        cast(AsyncSession, FakeExecutionSession([1, 3])), settings=settings
    )

    with pytest.raises(ResearchError) as user_error:
        await user_limited._assert_submission_quota(_OWNER_ID)
    with pytest.raises(ResearchError) as global_error:
        await global_limited._assert_submission_quota(_OWNER_ID)

    assert user_error.value.code is ResearchErrorCode.USER_QUOTA_EXCEEDED
    assert global_error.value.code is ResearchErrorCode.GLOBAL_BUDGET_EXHAUSTED


@pytest.mark.asyncio
async def test_researchable_document_gate_uses_collection_bibliography_entries() -> None:
    """研究会话可用性以集合书目条目和当前完成入库为准，不再要求 CollectionPaper。"""
    session = FakeExecutionSession([1])
    service = SqlAlchemyResearchConversationAdapter(cast(AsyncSession, session))

    await service._require_researchable_documents(_COLLECTION_ID)

    sql = _sql(session.scalar_statements[0])
    assert "collection_bibliography_entries" in sql
    assert "collection_papers" not in sql
    assert "bibliography_entry_id" in sql


@pytest.mark.asyncio
async def test_evidence_scope_guard_uses_collection_bibliography_entries() -> None:
    """落盘回答引用前的最终范围校验也必须允许没有全局 Paper 的集合文档。"""
    run = _run()
    session = FakeExecutionSession([], scalars_values=[[_CHUNK_ID]])

    await SqlAlchemyResearchExecutionAdapter(cast(AsyncSession, session))._assert_evidence_scope(
        run, (_evidence(),)
    )

    sql = _sql(session.scalars_statements[0])
    assert "collection_bibliography_entries" in sql
    assert "collection_papers" not in sql
    assert "bibliography_entry_id" in sql
