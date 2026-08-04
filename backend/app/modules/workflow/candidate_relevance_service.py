"""候选相关性运行级重试与取消服务。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from app.db.models.workflow import SearchRun
from app.modules.search.contracts import (
    CandidateRelevanceLevel,
    CandidateRelevanceState,
    UnifiedCandidate,
)
from app.modules.workflow.candidate_relevance import (
    mark_candidate_relevance_failed,
    mark_candidate_relevance_insufficient,
    skip_candidate_relevance,
)
from app.modules.workflow.contracts import SearchProgressEvent
from app.modules.workflow.job_queue import (
    CandidateRelevanceJobQueue,
    CandidateRelevanceQueueError,
)
from app.modules.workflow.search_run_service import SearchRunService
from app.modules.workflow.search_session import SearchSessionStore
from app.modules.workflow.state import SearchRunStage, SearchRunStatus
from sqlalchemy.ext.asyncio import AsyncSession


class CandidateRelevanceRunErrorCode(StrEnum):
    """运行级相关性控制接口的稳定错误码。"""

    SESSION_EXPIRED = "candidate_relevance_session_expired"
    RUN_NOT_RETRYABLE = "candidate_relevance_run_not_retryable"
    RUN_NOT_CANCELLABLE = "candidate_relevance_run_not_cancellable"
    QUEUE_UNAVAILABLE = "candidate_relevance_queue_unavailable"


class CandidateRelevanceRunError(RuntimeError):
    """整批重试或取消的范围、状态与队列错误。"""

    def __init__(self, code: CandidateRelevanceRunErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CandidateRelevanceRunResult:
    """返回当前 Redis 快照和持久化运行，前端只需刷新既有查询。"""

    search_run: SearchRun
    snapshot: dict[str, Any]


class CandidateRelevanceService:
    """重跑或取消一次完整候选集合的相关性分析，绝不触发 Provider 重检。"""

    def __init__(
        self,
        session: AsyncSession,
        session_store: SearchSessionStore,
        queue: CandidateRelevanceJobQueue | None = None,
    ) -> None:
        self._session = session
        self._session_store = session_store
        self._queue = queue

    async def retry(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> CandidateRelevanceRunResult:
        """复用当前统一候选快照，重新分析所有有摘要且已初筛纳入的候选。"""
        workflow_service = SearchRunService(self._session)
        run = await workflow_service.get_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
        session_key = self._session_key(run)
        snapshot = await self._snapshot_or_expire(run)
        if (
            run.status == SearchRunStatus.RUNNING.value
            and run.stage == SearchRunStage.RELEVANCE_ASSESSMENT.value
        ):
            return CandidateRelevanceRunResult(search_run=run, snapshot=snapshot)
        if run.status not in {
            SearchRunStatus.COMPLETED.value,
            SearchRunStatus.PARTIAL_FAILED.value,
            SearchRunStatus.CANCELLED.value,
        }:
            raise CandidateRelevanceRunError(
                CandidateRelevanceRunErrorCode.RUN_NOT_RETRYABLE,
                "当前检索运行不能重新分析候选理由。",
            )
        current_candidates = self._deserialize_candidates(snapshot)
        if not any(
            candidate.triage is not None and candidate.triage.included and candidate.abstract
            for candidate in current_candidates
        ):
            raise CandidateRelevanceRunError(
                CandidateRelevanceRunErrorCode.RUN_NOT_RETRYABLE,
                "当前候选均缺少摘要，没有可重新分析的内容。",
            )

        merged = await self._session_store.merge_snapshot(
            session_key,
            self._reset_relevance_snapshot,
        )
        candidates = self._deserialize_candidates(merged)
        reopened = await workflow_service.reopen_relevance_run(
            search_run_id=run.id,
            candidate_counts=self._candidate_counts(merged, candidates),
        )
        if reopened is None:
            raise CandidateRelevanceRunError(
                CandidateRelevanceRunErrorCode.RUN_NOT_RETRYABLE,
                "当前检索运行状态已变化，请刷新后重试。",
            )
        await self._session_store.clear_relevance_cancellation(session_key)
        attempt_id = uuid4().hex
        try:
            job_id = await self._queue_or_raise().enqueue_relevance(
                search_run_id=run.id,
                attempt_id=attempt_id,
            )
        except CandidateRelevanceQueueError as exc:
            await workflow_service.complete_run(
                search_run_id=run.id,
                status=SearchRunStatus.PARTIAL_FAILED,
                provider_summary=reopened.provider_summary,
                candidate_counts=self._candidate_counts(merged, candidates),
                error_code=CandidateRelevanceRunErrorCode.QUEUE_UNAVAILABLE.value,
                error_message="候选相关性任务无法投递，请稍后重新分析。",
            )
            raise CandidateRelevanceRunError(
                CandidateRelevanceRunErrorCode.QUEUE_UNAVAILABLE,
                "候选相关性任务无法投递，请稍后重新分析。",
            ) from exc
        # ``arq_job_id`` 属于原始 Provider 检索任务，不覆盖为 relevance Job。
        # 尝试 ID 已包含在新的 Job ID 中。
        _ = job_id
        await self._publish(
            run=reopened,
            snapshot=merged,
            message="正在基于当前完整候选集合重新分析相关性。",
        )
        return CandidateRelevanceRunResult(search_run=reopened, snapshot=merged)

    async def cancel(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> CandidateRelevanceRunResult:
        """请求安全取消，未完成候选转换为可整批重试的明确失败。"""
        workflow_service = SearchRunService(self._session)
        run = await workflow_service.get_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
        if (
            run.status != SearchRunStatus.RUNNING.value
            or run.stage != SearchRunStage.RELEVANCE_ASSESSMENT.value
        ):
            raise CandidateRelevanceRunError(
                CandidateRelevanceRunErrorCode.RUN_NOT_CANCELLABLE,
                "当前没有可取消的候选相关性分析。",
            )
        session_key = self._session_key(run)
        await self._session_store.request_relevance_cancellation(session_key)
        merged = await self._session_store.merge_snapshot(
            session_key, self._cancel_relevance_snapshot
        )
        candidates = self._deserialize_candidates(merged)
        cancelled = await workflow_service.cancel_relevance_run(
            search_run_id=run.id,
            candidate_counts=self._candidate_counts(merged, candidates),
        )
        if cancelled is None:
            raise CandidateRelevanceRunError(
                CandidateRelevanceRunErrorCode.RUN_NOT_CANCELLABLE,
                "相关性分析状态已变化，请刷新页面。",
            )
        await self._publish(
            run=cancelled,
            snapshot=merged,
            message="候选相关性分析已取消，可基于当前候选集合重新分析。",
        )
        return CandidateRelevanceRunResult(search_run=cancelled, snapshot=merged)

    async def _snapshot_or_expire(self, run: SearchRun) -> dict[str, Any]:
        snapshot = await self._session_store.read_snapshot(self._session_key(run))
        if snapshot is not None:
            return snapshot
        await SearchRunService(self._session).expire_run(run.id)
        raise CandidateRelevanceRunError(
            CandidateRelevanceRunErrorCode.SESSION_EXPIRED,
            "检索候选已过期，请重新执行文献检索。",
        )

    @staticmethod
    def _session_key(run: SearchRun) -> str:
        if run.redis_session_key is None:
            raise CandidateRelevanceRunError(
                CandidateRelevanceRunErrorCode.SESSION_EXPIRED,
                "检索候选会话不存在，请重新执行文献检索。",
            )
        return run.redis_session_key

    @staticmethod
    def _deserialize_candidates(snapshot: dict[str, Any]) -> tuple[UnifiedCandidate, ...]:
        raw_candidates = snapshot.get("candidates")
        if not isinstance(raw_candidates, list):
            raise CandidateRelevanceRunError(
                CandidateRelevanceRunErrorCode.SESSION_EXPIRED,
                "检索候选快照格式无效，请重新执行文献检索。",
            )
        return tuple(UnifiedCandidate.model_validate(item) for item in raw_candidates)

    @classmethod
    def _reset_relevance_snapshot(cls, snapshot: dict[str, Any]) -> dict[str, Any]:
        candidates = cls._deserialize_candidates(snapshot)
        updated = tuple(cls._pending_or_deterministic(candidate) for candidate in candidates)
        return cls._snapshot_with_candidates(snapshot, updated, status=SearchRunStatus.RUNNING)

    @classmethod
    def _cancel_relevance_snapshot(cls, snapshot: dict[str, Any]) -> dict[str, Any]:
        candidates = cls._deserialize_candidates(snapshot)
        updated = tuple(
            mark_candidate_relevance_failed(
                candidate,
                "候选相关性分析已取消，可重新分析当前完整候选集合。",
                code="candidate_relevance_cancelled",
                retryable=True,
            )
            if candidate.relevance_state is CandidateRelevanceState.PENDING
            else candidate
            for candidate in candidates
        )
        return cls._snapshot_with_candidates(snapshot, updated, status=SearchRunStatus.CANCELLED)

    @staticmethod
    def _pending_or_deterministic(candidate: UnifiedCandidate) -> UnifiedCandidate:
        if candidate.triage is None or not candidate.triage.included:
            return skip_candidate_relevance(candidate)
        if not candidate.abstract:
            return mark_candidate_relevance_insufficient(candidate)
        return candidate.model_copy(
            update={
                "relevance_state": CandidateRelevanceState.PENDING,
                "relevance_assessment": None,
                "relevance_error": None,
            }
        )

    @classmethod
    def _snapshot_with_candidates(
        cls,
        snapshot: dict[str, Any],
        candidates: tuple[UnifiedCandidate, ...],
        *,
        status: SearchRunStatus,
    ) -> dict[str, Any]:
        updated = dict(snapshot)
        updated["status"] = status.value
        updated["stage"] = (
            SearchRunStage.RELEVANCE_ASSESSMENT.value
            if status is SearchRunStatus.RUNNING
            else SearchRunStage.COMPLETED.value
        )
        updated["candidate_counts"] = cls._candidate_counts(updated, candidates)
        updated["candidates"] = [candidate.model_dump(mode="json") for candidate in candidates]
        return updated

    @staticmethod
    def _candidate_counts(
        snapshot: dict[str, Any],
        candidates: tuple[UnifiedCandidate, ...],
    ) -> dict[str, Any]:
        existing = snapshot.get("candidate_counts")
        counts = dict(existing) if isinstance(existing, dict) else {}
        counts["relevance_total_count"] = sum(
            candidate.triage is not None and candidate.triage.included for candidate in candidates
        )
        counts["relevance_pending_count"] = sum(
            candidate.relevance_state is CandidateRelevanceState.PENDING for candidate in candidates
        )
        counts["relevance_completed_count"] = sum(
            candidate.relevance_state is CandidateRelevanceState.COMPLETED
            for candidate in candidates
        )
        counts["relevance_insufficient_count"] = sum(
            candidate.relevance_assessment is not None
            and candidate.relevance_assessment.level
            is CandidateRelevanceLevel.INSUFFICIENT_INFORMATION
            for candidate in candidates
        )
        counts["relevance_failed_count"] = sum(
            candidate.relevance_state is CandidateRelevanceState.FAILED for candidate in candidates
        )
        return counts

    async def _publish(
        self,
        *,
        run: SearchRun,
        snapshot: dict[str, Any],
        message: str,
    ) -> None:
        await self._session_store.append_event(
            self._session_key(run),
            SearchProgressEvent(
                run_id=run.id,
                status=SearchRunStatus(snapshot.get("status", run.status)),
                stage=SearchRunStage(snapshot.get("stage", run.stage)),
                provider_summary=snapshot.get("provider_summary", run.provider_summary),
                candidate_counts=snapshot.get("candidate_counts", {}),
                message=message,
            ).model_dump(mode="json"),
        )

    def _queue_or_raise(self) -> CandidateRelevanceJobQueue:
        if self._queue is None:
            raise CandidateRelevanceQueueError("候选相关性服务缺少任务队列。")
        return self._queue
