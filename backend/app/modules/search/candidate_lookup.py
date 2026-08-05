"""从受所有权保护的 Redis 搜索会话中读取候选文献。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.modules.search.contracts import UnifiedCandidate
from app.modules.search.run_models import SearchRunRecord
from app.modules.search.run_repository import SearchRunRepository
from app.modules.search.run_service import SearchRunService
from app.modules.search.session import SearchSessionStore


class SearchCandidateLookupErrorCode(StrEnum):
    """候选读取失败的稳定原因码，可由不同业务流程分别映射为 HTTP 响应。"""

    CANDIDATE_NOT_FOUND = "candidate_not_found"
    CANDIDATE_NOT_ELIGIBLE = "candidate_not_eligible"
    SESSION_EXPIRED = "candidate_session_expired"


class SearchCandidateLookupError(RuntimeError):
    """候选不属于当前会话、会话过期或未通过初筛时抛出的明确错误。"""

    def __init__(self, code: SearchCandidateLookupErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SearchCandidateLookup:
    """一次候选读取的服务端运行上下文，绝不包含来自前端的文献字段。"""

    search_run: SearchRunRecord
    candidate: UnifiedCandidate


class SearchCandidateLookupService:
    """统一执行检索运行所有权、Redis 快照和候选初筛的读取边界。"""

    def __init__(self, runs: SearchRunRepository, session_store: SearchSessionStore) -> None:
        self._run_service = SearchRunService(runs)
        self._session_store = session_store

    async def get(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        candidate_id: UUID,
        require_included: bool = False,
    ) -> SearchCandidateLookup:
        """读取当前用户拥有的候选，按调用场景决定是否要求通过初筛。"""
        run = await self._run_service.get_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
        candidate = await self._load_candidate(run, candidate_id, require_included=require_included)
        return SearchCandidateLookup(search_run=run, candidate=candidate)

    async def _load_candidate(
        self,
        run: SearchRunRecord,
        candidate_id: UUID,
        *,
        require_included: bool,
    ) -> UnifiedCandidate:
        """从 Redis 快照精确读取候选，拒绝客户端伪造的标题、DOI 与 URL。"""
        if run.redis_session_key is None:
            raise SearchCandidateLookupError(
                SearchCandidateLookupErrorCode.SESSION_EXPIRED,
                "检索候选会话不存在，请重新执行文献检索。",
            )

        snapshot = await self._session_store.read_snapshot(run.redis_session_key)
        if snapshot is None:
            await self._run_service.expire_run(run.id)
            raise SearchCandidateLookupError(
                SearchCandidateLookupErrorCode.SESSION_EXPIRED,
                "检索候选已过期，请重新执行文献检索。",
            )

        raw_candidates = snapshot.get("candidates")
        if not isinstance(raw_candidates, list):
            raise SearchCandidateLookupError(
                SearchCandidateLookupErrorCode.SESSION_EXPIRED,
                "检索候选快照格式无效，请重新执行文献检索。",
            )

        for raw_candidate in raw_candidates:
            candidate = UnifiedCandidate.model_validate(raw_candidate)
            if candidate.candidate_id != candidate_id:
                continue
            if require_included and (candidate.triage is None or not candidate.triage.included):
                raise SearchCandidateLookupError(
                    SearchCandidateLookupErrorCode.CANDIDATE_NOT_ELIGIBLE,
                    "该候选未通过基础筛选，不能执行当前操作。",
                )
            return candidate

        raise SearchCandidateLookupError(
            SearchCandidateLookupErrorCode.CANDIDATE_NOT_FOUND,
            "当前检索运行中不存在该候选文献。",
        )
