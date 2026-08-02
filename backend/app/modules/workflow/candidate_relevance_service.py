"""失败候选的单项相关性重试服务。

候选相关性只保存在 Redis 搜索会话中。因此重试必须先校验工作区、搜索运行和
候选归属，再从服务端快照读取元数据，绝不接受前端重新提交标题或摘要。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from app.db.models.workflow import ResearchPlan, SearchRun
from app.modules.search.contracts import CandidateRelevanceState, UnifiedCandidate
from app.modules.workflow.candidate_relevance import (
    OpenAICompatibleCandidateRelevanceEvaluator,
    build_candidate_relevance_context,
    mark_candidate_relevance_failed,
)
from app.modules.workflow.query_plan import read_confirmed_query_plan
from app.modules.workflow.search_run_service import SearchRunService
from app.modules.workflow.search_session import (
    SearchSessionStore,
    build_candidate_relevance_retry_lock_key,
)
from app.modules.workflow.settings import get_workflow_settings
from app.modules.workflow.state import SearchRunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_RETRY_LOCK_TTL_SECONDS = 240


class CandidateRelevanceRetryErrorCode(StrEnum):
    """单项相关性重试的稳定业务错误码。"""

    CANDIDATE_NOT_FOUND = "candidate_relevance_not_found"
    SESSION_EXPIRED = "candidate_relevance_session_expired"
    SEARCH_NOT_FINISHED = "candidate_relevance_search_not_finished"
    NOT_RETRYABLE = "candidate_relevance_not_retryable"


class CandidateRelevanceRetryError(RuntimeError):
    """重试范围、会话状态或候选状态不合法时抛出的可展示错误。"""

    def __init__(self, code: CandidateRelevanceRetryErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CandidateRelevanceRetryResult:
    """单项重试结束后返回当前 Redis 快照，前端可直接替换列表。"""

    search_run: SearchRun
    snapshot: dict[str, Any]


class CandidateRelevanceService:
    """在完成检索的短期会话中安全重试单篇候选的语义评估。"""

    def __init__(
        self,
        session: AsyncSession,
        session_store: SearchSessionStore,
        *,
        evaluator: OpenAICompatibleCandidateRelevanceEvaluator | None = None,
    ) -> None:
        self._session = session
        self._session_store = session_store
        self._evaluator = evaluator

    async def retry(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        candidate_id: UUID,
    ) -> CandidateRelevanceRetryResult:
        """重试当前会话中失败且可重试的一条候选，不创建新的搜索运行。"""
        run = await SearchRunService(self._session).get_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
        self._require_finished_search(run)
        session_key = self._session_key(run)
        lock_key = build_candidate_relevance_retry_lock_key(session_key, candidate_id)
        lock_token = uuid4().hex
        acquired = await self._session_store.try_acquire_lock(
            lock_key,
            token=lock_token,
            ttl_seconds=_RETRY_LOCK_TTL_SECONDS,
        )
        if not acquired:
            # 重复点击不会叠加模型调用，直接返回当前会话快照供前端刷新状态。
            snapshot, candidates = await self._read_snapshot(run)
            self._candidate_from(candidates, candidate_id)
            return CandidateRelevanceRetryResult(search_run=run, snapshot=snapshot)

        try:
            snapshot, candidates = await self._read_snapshot(run)
            candidate = self._candidate_from(candidates, candidate_id)
            if candidate.relevance_state in {
                CandidateRelevanceState.PENDING,
                CandidateRelevanceState.COMPLETED,
            }:
                return CandidateRelevanceRetryResult(search_run=run, snapshot=snapshot)
            self._require_retryable(candidate)

            pending_candidate = candidate.model_copy(
                update={
                    "relevance_state": CandidateRelevanceState.PENDING,
                    "relevance_assessment": None,
                    "relevance_error": None,
                }
            )
            pending_snapshot = self._replace_candidate(snapshot, candidates, pending_candidate)
            await self._session_store.write_snapshot(session_key, pending_snapshot)

            resolved_candidate = await self._assess_one(run, candidate)
            final_snapshot = self._replace_candidate(
                pending_snapshot,
                self._deserialize_candidates(pending_snapshot),
                resolved_candidate,
            )
            await self._session_store.write_snapshot(session_key, final_snapshot)
            return CandidateRelevanceRetryResult(search_run=run, snapshot=final_snapshot)
        finally:
            await self._session_store.release_lock(lock_key, token=lock_token)

    async def _assess_one(self, run: SearchRun, candidate: UnifiedCandidate) -> UnifiedCandidate:
        """读取固定检索计划后只把当前候选交给结构化 Agent。"""
        plan = await self._session.scalar(
            select(ResearchPlan).where(ResearchPlan.id == run.research_plan_id)
        )
        if plan is None:
            return mark_candidate_relevance_failed(
                candidate,
                "检索计划已不存在，无法重新生成候选理由。",
                code="candidate_relevance_plan_missing",
            )
        try:
            query_specs, scope = read_confirmed_query_plan(plan)
            context = build_candidate_relevance_context(
                research_question=plan.raw_request,
                direction_options=plan.direction_options,
                selected_direction_id=plan.selected_direction_id,
                query_specs=query_specs,
                scope=scope,
            )
            evaluator = self._evaluator or OpenAICompatibleCandidateRelevanceEvaluator(
                get_workflow_settings()
            )
        except Exception:
            return mark_candidate_relevance_failed(
                candidate,
                "候选相关性模型尚未配置或暂时不可用，请稍后重试。",
                code="candidate_relevance_model_unavailable",
            )
        return (await evaluator.assess(context=context, candidates=(candidate,)))[0]

    async def _read_snapshot(
        self,
        run: SearchRun,
    ) -> tuple[dict[str, Any], tuple[UnifiedCandidate, ...]]:
        """读取并校验短期候选快照，过期时同步更新长期运行审计状态。"""
        session_key = self._session_key(run)
        snapshot = await self._session_store.read_snapshot(session_key)
        if snapshot is None:
            await SearchRunService(self._session).expire_run(run.id)
            raise CandidateRelevanceRetryError(
                CandidateRelevanceRetryErrorCode.SESSION_EXPIRED,
                "检索候选已过期，请重新执行文献检索。",
            )
        return snapshot, self._deserialize_candidates(snapshot)

    @staticmethod
    def _deserialize_candidates(snapshot: dict[str, Any]) -> tuple[UnifiedCandidate, ...]:
        """验证 Redis 候选格式，损坏快照不允许被局部重写。"""
        raw_candidates = snapshot.get("candidates")
        if not isinstance(raw_candidates, list):
            raise CandidateRelevanceRetryError(
                CandidateRelevanceRetryErrorCode.SESSION_EXPIRED,
                "检索候选快照格式无效，请重新执行文献检索。",
            )
        return tuple(UnifiedCandidate.model_validate(item) for item in raw_candidates)

    @staticmethod
    def _candidate_from(
        candidates: tuple[UnifiedCandidate, ...],
        candidate_id: UUID,
    ) -> UnifiedCandidate:
        """按候选 UUID 精确定位，避免客户端依赖标题或 DOI 进行匹配。"""
        candidate = next(
            (item for item in candidates if item.candidate_id == candidate_id),
            None,
        )
        if candidate is None:
            raise CandidateRelevanceRetryError(
                CandidateRelevanceRetryErrorCode.CANDIDATE_NOT_FOUND,
                "当前检索运行中不存在该候选文献。",
            )
        return candidate

    @staticmethod
    def _require_finished_search(run: SearchRun) -> None:
        """只允许终态搜索会话被单项补算，避免与搜索 Worker 争写同一快照。"""
        if run.status not in {
            SearchRunStatus.COMPLETED.value,
            SearchRunStatus.PARTIAL_FAILED.value,
        }:
            raise CandidateRelevanceRetryError(
                CandidateRelevanceRetryErrorCode.SEARCH_NOT_FINISHED,
                "文献检索尚未完成，暂时不能重试候选相关性分析。",
            )

    @staticmethod
    def _require_retryable(candidate: UnifiedCandidate) -> None:
        """只重试明确失败且服务端标记为可重试的模型评估。"""
        if candidate.relevance_state is CandidateRelevanceState.SKIPPED:
            raise CandidateRelevanceRetryError(
                CandidateRelevanceRetryErrorCode.NOT_RETRYABLE,
                "该候选未通过基础筛选，未进入相关性分析，不能单独重试。",
            )
        if (
            candidate.relevance_state is not CandidateRelevanceState.FAILED
            or candidate.relevance_error is None
            or not candidate.relevance_error.retryable
        ):
            raise CandidateRelevanceRetryError(
                CandidateRelevanceRetryErrorCode.NOT_RETRYABLE,
                "该候选当前不处于可重试的相关性失败状态。",
            )

    @staticmethod
    def _session_key(run: SearchRun) -> str:
        """Redis 会话键必须由持久化搜索运行提供，不能来自 URL 参数。"""
        if run.redis_session_key is None:
            raise CandidateRelevanceRetryError(
                CandidateRelevanceRetryErrorCode.SESSION_EXPIRED,
                "检索候选会话不存在，请重新执行文献检索。",
            )
        return run.redis_session_key

    @staticmethod
    def _replace_candidate(
        snapshot: dict[str, Any],
        candidates: tuple[UnifiedCandidate, ...],
        replacement: UnifiedCandidate,
    ) -> dict[str, Any]:
        """生成新的快照和统计，避免原地修改调用方持有的 Redis 数据。"""
        updated_candidates = tuple(
            replacement if item.candidate_id == replacement.candidate_id else item
            for item in candidates
        )
        updated_snapshot = dict(snapshot)
        candidate_counts = snapshot.get("candidate_counts")
        if not isinstance(candidate_counts, dict):
            raise CandidateRelevanceRetryError(
                CandidateRelevanceRetryErrorCode.SESSION_EXPIRED,
                "检索候选快照缺少处理统计，请重新执行文献检索。",
            )
        updated_counts = dict(candidate_counts)
        updated_counts["relevance_total_count"] = sum(
            item.triage is not None and item.triage.included for item in updated_candidates
        )
        updated_counts["relevance_completed_count"] = sum(
            item.relevance_state is CandidateRelevanceState.COMPLETED for item in updated_candidates
        )
        updated_counts["relevance_failed_count"] = sum(
            item.relevance_state is CandidateRelevanceState.FAILED for item in updated_candidates
        )
        updated_snapshot["candidate_counts"] = updated_counts
        updated_snapshot["candidates"] = [
            item.model_dump(mode="json") for item in updated_candidates
        ]
        return updated_snapshot
