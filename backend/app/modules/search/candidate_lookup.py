"""从受所有权保护的持久候选审核投影中读取候选文献。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.modules.search.candidate_repository import SearchCandidateRepository
from app.modules.search.contracts import UnifiedCandidate
from app.modules.search.run_models import SearchRunRecord
from app.modules.search.run_repository import SearchRunRepository
from app.modules.search.run_service import SearchRunService


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
    """统一执行检索运行所有权、持久候选和候选初筛的读取边界。"""

    def __init__(self, runs: SearchRunRepository, candidates: SearchCandidateRepository) -> None:
        self._run_service = SearchRunService(runs)
        self._candidates = candidates

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
        """从持久候选投影精确读取，拒绝客户端伪造的标题、DOI 与 URL。"""
        candidate = await self._candidates.get_candidate(
            search_run_id=run.id,
            candidate_id=candidate_id,
        )
        if candidate is not None:
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
