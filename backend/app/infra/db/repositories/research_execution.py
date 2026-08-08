"""RAG Worker 领取、推进和落盘研究运行的持久化服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.collection import CollectionBibliographyEntry, ResearchCollection
from app.infra.db.models.document import Document, DocumentChunk, IngestionRun
from app.infra.db.models.research import Conversation, Message, ResearchEvidence, ResearchRun
from app.modules.rag.retrieval import RetrievedEvidence
from app.modules.research.contracts import (
    ResearchRunStage,
    ResearchRunStatus,
)
from app.modules.research.execution_port import ResearchExecutionContext, ResearchOutcome


class SqlAlchemyResearchExecutionAdapter:
    """把外部模型与向量调用前后的运行状态分割为短事务。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, research_run_id: UUID) -> ResearchExecutionContext | None:
        """原子领取 queued 运行；重复 arq 消息不会重跑已领取或终态任务。"""
        async with self._session.begin():
            row = (
                await self._session.execute(
                    select(ResearchRun, Message, Conversation, ResearchCollection)
                    .join(Message, Message.id == ResearchRun.input_message_id)
                    .join(Conversation, Conversation.id == ResearchRun.conversation_id)
                    .join(ResearchCollection, ResearchCollection.id == ResearchRun.collection_id)
                    .where(ResearchRun.id == research_run_id)
                    .with_for_update(of=ResearchRun)
                )
            ).one_or_none()
            if row is None:
                return None
            run, input_message, conversation, collection = row._tuple()
            if run.status != ResearchRunStatus.QUEUED.value:
                return None
            if (
                conversation.status != "active"
                or collection.status != "active"
                or conversation.collection_id != collection.id
            ):
                run.status = ResearchRunStatus.CANCELLED.value
                run.stage = ResearchRunStage.CANCELLED.value
                run.finished_at = datetime.now(UTC)
                return None
            run.status = ResearchRunStatus.RUNNING.value
            run.stage = ResearchRunStage.PREPARING.value
            started_at = datetime.now(UTC)
            run.cancel_requested_at = None
            run.started_at = started_at
            run.stage_started_at = started_at
            run.finished_at = None
            run.error_code = None
            run.error_message = None
            run.retrieval_trace = {
                **run.retrieval_trace,
                "stage": run.stage,
                "timing": {
                    "started_at": started_at.isoformat(),
                    "current_stage": run.stage,
                    "stage_started_at": started_at.isoformat(),
                    "stages": [],
                },
            }
            return ResearchExecutionContext(
                research_run_id=run.id,
                conversation_id=conversation.id,
                collection_id=collection.id,
                owner_user_id=collection.owner_user_id,
                question=input_message.content,
                mode=run.mode,
                langgraph_thread_id=run.langgraph_thread_id or f"research-{run.id}",
                model_config=dict(run.model_config),
            )

    async def set_stage(self, research_run_id: UUID, stage: ResearchRunStage) -> bool:
        """以短事务推进公开阶段；取消后迟到的图节点不会覆盖终态。"""
        async with self._session.begin():
            run = await self._session.scalar(
                select(ResearchRun).where(ResearchRun.id == research_run_id).with_for_update()
            )
            if (
                run is None
                or run.status != ResearchRunStatus.RUNNING.value
                or run.cancel_requested_at is not None
            ):
                return False
            if run.stage == stage.value:
                return True
            now = datetime.now(UTC)
            timing = self._advance_timing(run, now, next_stage=stage)
            run.stage = stage.value
            run.stage_started_at = now
            run.retrieval_trace = {**run.retrieval_trace, "stage": stage.value, "timing": timing}
            return True

    async def is_cancel_requested(self, research_run_id: UUID) -> bool:
        """供图节点在模型或检索调用的前后读取持久化取消请求。"""
        run = await self._session.scalar(
            select(ResearchRun).where(ResearchRun.id == research_run_id)
        )
        return (
            run is None
            or run.status != ResearchRunStatus.RUNNING.value
            or run.cancel_requested_at is not None
        )

    async def finalize_cancellation(self, research_run_id: UUID) -> bool:
        """只在 Worker 到达安全边界后把 running 运行确认为 cancelled。"""
        async with self._session.begin():
            run = await self._session.scalar(
                select(ResearchRun).where(ResearchRun.id == research_run_id).with_for_update()
            )
            if (
                run is None
                or run.status != ResearchRunStatus.RUNNING.value
                or run.cancel_requested_at is None
            ):
                return False
            self._mark_cancelled(run, datetime.now(UTC))
            return True

    async def finalize_requested_cancellations(self) -> tuple[UUID, ...]:
        """Worker 重启时收敛已请求取消但来不及确认的 running 运行。"""
        async with self._session.begin():
            runs = list(
                await self._session.scalars(
                    select(ResearchRun)
                    .where(
                        ResearchRun.status == ResearchRunStatus.RUNNING.value,
                        ResearchRun.cancel_requested_at.is_not(None),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            finished_at = datetime.now(UTC)
            for run in runs:
                self._mark_cancelled(run, finished_at)
            return tuple(run.id for run in runs)

    async def complete(
        self, research_run_id: UUID, outcome: ResearchOutcome
    ) -> ResearchRunStatus | None:
        """保存答案、证据和最终状态；所有引用都再次关联当前版本的 PostgreSQL 块。"""
        async with self._session.begin():
            run = await self._session.scalar(
                select(ResearchRun).where(ResearchRun.id == research_run_id).with_for_update()
            )
            if run is None or run.status != ResearchRunStatus.RUNNING.value:
                return None
            if run.cancel_requested_at is not None:
                self._mark_cancelled(run, datetime.now(UTC))
                return ResearchRunStatus.CANCELLED
            await self._session.execute(
                delete(ResearchEvidence).where(ResearchEvidence.research_run_id == research_run_id)
            )
            await self._assert_evidence_scope(run, outcome.evidences)
            evidence_records = self._evidence_records(run.id, outcome)
            self._session.add_all(evidence_records)
            output_message = Message(
                id=uuid4(),
                conversation_id=run.conversation_id,
                role="assistant",
                content=outcome.answer,
                status="completed",
                metadata_json={
                    "research_run_id": str(run.id),
                    "outcome": "clarification"
                    if outcome.status is ResearchRunStatus.AWAITING_CLARIFICATION
                    else "answer",
                },
            )
            self._session.add(output_message)
            run.output_message_id = output_message.id
            run.mode = outcome.mode
            finished_at = datetime.now(UTC)
            terminal_trace = self._terminal_trace(
                run, outcome.retrieval_trace, finished_at=finished_at
            )
            run.status = outcome.status.value
            run.stage = outcome.stage.value
            run.retrieval_trace = terminal_trace
            run.finished_at = finished_at
            run.stage_started_at = None
            conversation = await self._session.scalar(
                select(Conversation).where(Conversation.id == run.conversation_id).with_for_update()
            )
            if conversation is not None:
                conversation.updated_at = finished_at
            return outcome.status

    async def fail(
        self,
        research_run_id: UUID,
        *,
        code: str,
        message: str,
        diagnostics: dict[str, Any] | None = None,
    ) -> ResearchRunStatus | None:
        """持久化非预期异常，前端可以在同一运行上发起显式重试。"""
        async with self._session.begin():
            run = await self._session.scalar(
                select(ResearchRun).where(ResearchRun.id == research_run_id).with_for_update()
            )
            if run is None or run.status != ResearchRunStatus.RUNNING.value:
                return None
            if run.cancel_requested_at is not None:
                self._mark_cancelled(run, datetime.now(UTC))
                return ResearchRunStatus.CANCELLED
            finished_at = datetime.now(UTC)
            terminal_trace = self._terminal_trace(
                run,
                {
                    **run.retrieval_trace,
                    "stage": ResearchRunStage.FAILED.value,
                    **(
                        {
                            "failure_diagnostics": {
                                "failure_code": code,
                                **diagnostics,
                            }
                        }
                        if diagnostics
                        else {}
                    ),
                },
                finished_at=finished_at,
            )
            run.status = ResearchRunStatus.FAILED.value
            run.stage = ResearchRunStage.FAILED.value
            run.error_code = code
            run.error_message = message
            run.finished_at = finished_at
            run.retrieval_trace = terminal_trace
            run.stage_started_at = None
            return ResearchRunStatus.FAILED

    @staticmethod
    def _advance_timing(
        run: ResearchRun, finished_at: datetime, *, next_stage: ResearchRunStage | None
    ) -> dict[str, Any]:
        """关闭当前公开阶段并保留每阶段耗时，不依赖前端计时。"""
        existing = run.retrieval_trace.get("timing")
        timing = dict(existing) if isinstance(existing, dict) else {}
        raw_stages = timing.get("stages")
        stages = (
            [dict(item) for item in raw_stages if isinstance(item, dict)]
            if isinstance(raw_stages, list)
            else []
        )
        if run.stage_started_at is not None:
            stages.append(
                {
                    "stage": run.stage,
                    "started_at": run.stage_started_at.isoformat(),
                    "finished_at": finished_at.isoformat(),
                    "duration_ms": max(
                        0, int((finished_at - run.stage_started_at).total_seconds() * 1_000)
                    ),
                }
            )
        timing["stages"] = stages
        if next_stage is None:
            timing["finished_at"] = finished_at.isoformat()
            if run.started_at is not None:
                timing["total_duration_ms"] = max(
                    0, int((finished_at - run.started_at).total_seconds() * 1_000)
                )
            timing.pop("current_stage", None)
            timing.pop("stage_started_at", None)
        else:
            timing["current_stage"] = next_stage.value
            timing["stage_started_at"] = finished_at.isoformat()
        return timing

    def _terminal_trace(
        self, run: ResearchRun, trace: dict[str, Any], *, finished_at: datetime
    ) -> dict[str, Any]:
        """合并图审计与服务端阶段计时，保留提交时记录的配额快照。"""
        previous_governance = run.retrieval_trace.get("governance")
        next_governance = trace.get("governance")
        governance = {
            **(dict(previous_governance) if isinstance(previous_governance, dict) else {}),
            **(dict(next_governance) if isinstance(next_governance, dict) else {}),
        }
        return {
            **trace,
            **({"governance": governance} if governance else {}),
            "timing": self._advance_timing(run, finished_at, next_stage=None),
        }

    def _mark_cancelled(self, run: ResearchRun, finished_at: datetime) -> None:
        """确认取消时不写回答或证据，只关闭当前阶段的审计计时。"""
        timing = self._advance_timing(run, finished_at, next_stage=None)
        run.status = ResearchRunStatus.CANCELLED.value
        run.stage = ResearchRunStage.CANCELLED.value
        run.error_code = None
        run.error_message = None
        run.finished_at = finished_at
        run.stage_started_at = None
        run.retrieval_trace = {
            **run.retrieval_trace,
            "stage": ResearchRunStage.CANCELLED.value,
            "cancellation": {
                "state": "confirmed",
                "requested_at": run.cancel_requested_at.isoformat()
                if run.cancel_requested_at is not None
                else None,
                "confirmed_at": finished_at.isoformat(),
            },
            "timing": timing,
        }

    async def _assert_evidence_scope(
        self, run: ResearchRun, evidences: tuple[RetrievedEvidence, ...]
    ) -> None:
        """在写引用前再检查每个片段仍属于当前集合的 current 完成版本。"""
        if not evidences:
            return
        allowed_ids = set(
            await self._session.scalars(
                select(DocumentChunk.id)
                .join(IngestionRun, IngestionRun.id == DocumentChunk.ingestion_run_id)
                .join(Document, Document.id == IngestionRun.document_id)
                .join(
                    CollectionBibliographyEntry,
                    and_(
                        CollectionBibliographyEntry.collection_id == Document.collection_id,
                        CollectionBibliographyEntry.id == Document.bibliography_entry_id,
                    ),
                )
                .where(
                    DocumentChunk.id.in_([evidence.chunk_id for evidence in evidences]),
                    Document.collection_id == run.collection_id,
                    CollectionBibliographyEntry.status == "active",
                    IngestionRun.status == "completed",
                    IngestionRun.is_current.is_(True),
                )
            )
        )
        unexpected = {evidence.chunk_id for evidence in evidences} - allowed_ids
        if unexpected:
            raise RuntimeError("研究运行试图保存超出当前集合或旧版本的证据片段。")

    @staticmethod
    def _evidence_records(
        research_run_id: UUID, outcome: ResearchOutcome
    ) -> list[ResearchEvidence]:
        """同时保存 RRF 入选池和最终引用，便于后续定位回答为何选中某个片段。"""
        records: list[ResearchEvidence] = []
        cited_chunk_ids = set(outcome.cited_chunk_ids)
        citation_by_chunk_id = {
            UUID(str(item["chunk_id"])): item
            for item in outcome.retrieval_trace.get("user_citations", [])
            if isinstance(item, dict) and item.get("chunk_id") is not None
        }
        for evidence in outcome.evidences:
            selection_stage = "rerank" if evidence.rerank_score is not None else "rrf"
            locator = {
                **evidence.locator,
                "page_start": evidence.page_start,
                "page_end": evidence.page_end,
                "section_path": list(evidence.section_path),
                "parent_merged": evidence.parent_merged,
                "source_chunk_ids": [str(chunk_id) for chunk_id in evidence.source_chunk_ids],
            }
            records.append(
                ResearchEvidence(
                    research_run_id=research_run_id,
                    chunk_id=evidence.chunk_id,
                    selection_stage=selection_stage,
                    rank=evidence.rank,
                    vector_score=evidence.vector_score,
                    rrf_score=evidence.rrf_score,
                    rerank_score=evidence.rerank_score,
                    is_cited=evidence.chunk_id in cited_chunk_ids,
                    citation_excerpt=evidence.content,
                    locator_snapshot=locator,
                )
            )
            if evidence.chunk_id in cited_chunk_ids:
                citation = citation_by_chunk_id.get(evidence.chunk_id, {})
                display_index = citation.get("display_index")
                evidence_ref = citation.get("evidence_ref")
                final_locator = {
                    **locator,
                    **({"display_index": display_index} if isinstance(display_index, int) else {}),
                    **({"evidence_ref": evidence_ref} if isinstance(evidence_ref, str) else {}),
                }
                records.append(
                    ResearchEvidence(
                        research_run_id=research_run_id,
                        chunk_id=evidence.chunk_id,
                        selection_stage="final_citation",
                        rank=display_index if isinstance(display_index, int) else evidence.rank,
                        vector_score=evidence.vector_score,
                        rrf_score=evidence.rrf_score,
                        rerank_score=evidence.rerank_score,
                        is_cited=True,
                        citation_excerpt=evidence.content,
                        locator_snapshot=final_locator,
                    )
                )
        return records
