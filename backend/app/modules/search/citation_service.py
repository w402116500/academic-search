"""将当前持久候选审核投影中的题录渲染为正式引用。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.literature.api_contracts import (
    CandidateCitationError,
    CandidateCitationErrorCode,
)
from app.modules.literature.citation_formatter import (
    CitationFormat,
    CitationFormattingError,
    format_citation,
)
from app.modules.literature.contracts import CitationMetadataStatus
from app.modules.search.candidate_lookup import (
    SearchCandidateLookupError,
    SearchCandidateLookupErrorCode,
    SearchCandidateLookupService,
)
from app.modules.search.candidate_repository import SearchCandidateRepository
from app.modules.search.contracts import UnifiedCandidate
from app.modules.search.run_models import SearchRunRecord
from app.modules.search.run_repository import SearchRunRepository


@dataclass(frozen=True, slots=True)
class CandidateCitationRender:
    """正式引用的受控输出，不泄漏 Redis 会话键或未核验原始数据。"""

    candidate_id: UUID
    format: CitationFormat
    text: str


class CandidateCitationService:
    """只允许从当前用户拥有的候选审核事实生成格式化引用。"""

    def __init__(self, runs: SearchRunRepository, candidates: SearchCandidateRepository) -> None:
        self._candidates = candidates
        self._lookup = SearchCandidateLookupService(runs, candidates)

    async def render(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        candidate_id: UUID,
        citation_format: CitationFormat,
    ) -> CandidateCitationRender:
        """仅将 `ready` 题录交给 CSL 或 BibTeX 引擎，绝不接受前端题录字段。"""
        try:
            lookup = await self._lookup.get(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                search_run_id=search_run_id,
                candidate_id=candidate_id,
            )
        except SearchCandidateLookupError as exc:
            code = {
                SearchCandidateLookupErrorCode.CANDIDATE_NOT_FOUND: (
                    CandidateCitationErrorCode.CANDIDATE_NOT_FOUND
                ),
                SearchCandidateLookupErrorCode.CANDIDATE_NOT_ELIGIBLE: (
                    CandidateCitationErrorCode.CANDIDATE_NOT_FOUND
                ),
                SearchCandidateLookupErrorCode.SESSION_EXPIRED: (
                    CandidateCitationErrorCode.SESSION_EXPIRED
                ),
            }[exc.code]
            raise CandidateCitationError(code, str(exc)) from exc

        candidate = await self._candidate_with_ready_citation(lookup.search_run, lookup.candidate)
        if candidate.citation is None:
            raise CandidateCitationError(
                CandidateCitationErrorCode.CITATION_NOT_READY,
                "该候选尚未取得可核验题录，暂时不能生成正式引用。",
            )

        try:
            text = format_citation(candidate.citation, citation_format)
        except CitationFormattingError as exc:
            raise CandidateCitationError(
                CandidateCitationErrorCode.CITATION_NOT_READY,
                str(exc),
            ) from exc

        return CandidateCitationRender(
            candidate_id=candidate.candidate_id,
            format=citation_format,
            text=text,
        )

    async def _candidate_with_ready_citation(
        self,
        search_run: SearchRunRecord,
        candidate: UnifiedCandidate,
    ) -> UnifiedCandidate:
        """全文 Worker 已补全题录时，优先使用同一候选的持久状态。"""
        if (
            candidate.citation is not None
            and candidate.citation.status is CitationMetadataStatus.READY
        ):
            return candidate
        state = await self._candidates.get_fulltext_state(
            search_run_id=search_run.id,
            candidate_id=candidate.candidate_id,
        )
        if state is None:
            return candidate

        if (
            state.search_run_id != search_run.id
            or state.candidate.candidate_id != candidate.candidate_id
            or state.candidate.citation is None
            or state.candidate.citation.status is not CitationMetadataStatus.READY
        ):
            return candidate
        if state.candidate.citation is None:
            return candidate
        return candidate.model_copy(update={"citation": state.candidate.citation})
