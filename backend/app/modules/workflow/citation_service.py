"""将当前搜索会话中的候选题录渲染为正式引用。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.search.citation_formatter import (
    CitationFormat,
    CitationFormattingError,
    format_citation,
)
from app.modules.workflow.candidate_lookup import (
    SearchCandidateLookupError,
    SearchCandidateLookupErrorCode,
    SearchCandidateLookupService,
)
from app.modules.workflow.contracts import CandidateCitationError, CandidateCitationErrorCode
from app.modules.workflow.search_session import SearchSessionStore
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class CandidateCitationRender:
    """正式引用的受控输出，不泄漏 Redis 会话键或未核验原始数据。"""

    candidate_id: UUID
    format: CitationFormat
    text: str


class CandidateCitationService:
    """只允许从当前用户拥有的候选会话生成格式化引用。"""

    def __init__(self, session: AsyncSession, session_store: SearchSessionStore) -> None:
        self._lookup = SearchCandidateLookupService(session, session_store)

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

        if lookup.candidate.citation is None:
            raise CandidateCitationError(
                CandidateCitationErrorCode.CITATION_NOT_READY,
                "该候选尚未取得可核验题录，暂时不能生成正式引用。",
            )

        try:
            text = format_citation(lookup.candidate.citation, citation_format)
        except CitationFormattingError as exc:
            raise CandidateCitationError(
                CandidateCitationErrorCode.CITATION_NOT_READY,
                str(exc),
            ) from exc

        return CandidateCitationRender(
            candidate_id=lookup.candidate.candidate_id,
            format=citation_format,
            text=text,
        )
