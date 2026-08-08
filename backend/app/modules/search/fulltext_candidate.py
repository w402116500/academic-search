"""Search-owned bridge from unified candidates to the Documents full-text boundary."""

from __future__ import annotations

from uuid import UUID

from app.modules.documents.api_contracts import (
    CandidateFulltextError,
    CandidateFulltextErrorCode,
)
from app.modules.documents.contracts import FulltextCandidate, FulltextCandidateLinks
from app.modules.search.candidate_lookup import (
    SearchCandidateLookupError,
    SearchCandidateLookupErrorCode,
    SearchCandidateLookupService,
)
from app.modules.search.candidate_repository import SearchCandidateRepository
from app.modules.search.contracts import UnifiedCandidate
from app.modules.search.run_repository import SearchRunRepository


def to_fulltext_candidate(candidate: UnifiedCandidate) -> FulltextCandidate:
    """Project one short-lived Search candidate into the Documents command shape."""
    return FulltextCandidate(
        candidate_id=candidate.candidate_id,
        doi=candidate.doi,
        abstract=candidate.abstract,
        links=FulltextCandidateLinks(
            landing_url=candidate.links.landing_url,
            open_access_url=candidate.links.open_access_url,
            fulltext_url=candidate.links.fulltext_url,
        ),
        is_open_access=candidate.is_open_access,
        citation=candidate.citation,
    )


class SearchCandidateFulltextLookup:
    """Resolve and project an eligible candidate without exposing Search DTOs."""

    def __init__(self, runs: SearchRunRepository, candidates: SearchCandidateRepository) -> None:
        self._lookup = SearchCandidateLookupService(runs, candidates)

    async def get(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        candidate_id: UUID,
    ) -> FulltextCandidate:
        try:
            lookup = await self._lookup.get(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                require_included=True,
            )
        except SearchCandidateLookupError as exc:
            code = {
                SearchCandidateLookupErrorCode.CANDIDATE_NOT_FOUND: (
                    CandidateFulltextErrorCode.CANDIDATE_NOT_FOUND
                ),
                SearchCandidateLookupErrorCode.CANDIDATE_NOT_ELIGIBLE: (
                    CandidateFulltextErrorCode.CANDIDATE_NOT_ELIGIBLE
                ),
                SearchCandidateLookupErrorCode.SESSION_EXPIRED: (
                    CandidateFulltextErrorCode.SESSION_EXPIRED
                ),
            }[exc.code]
            raise CandidateFulltextError(code, str(exc)) from exc
        return to_fulltext_candidate(lookup.candidate)
