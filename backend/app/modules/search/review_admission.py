"""候选审核中的批量文献准入用例。"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.modules.literature.citation_formatter import (
    CitationFormat,
    CitationFormattingError,
    format_citation,
)
from app.modules.literature.contracts import CitationMetadataStatus
from app.modules.research.bibliography import (
    BibliographyCitationStatus,
    BibliographyContentStatus,
    BibliographyPdfStatus,
    CollectionBibliographyEntryDraft,
    CollectionBibliographyError,
    CollectionBibliographyRepository,
    CollectionBibliographyUpsertStatus,
)
from app.modules.search.api_contracts import (
    CandidateAdmissionBatchResponse,
    CandidateAdmissionItem,
)
from app.modules.search.contracts import CandidatePdfAvailabilityStatus, UnifiedCandidate
from app.modules.search.review_selection import CandidateSelectionService
from app.modules.search.review_session import CandidateReviewSession

logger = logging.getLogger(__name__)


class CandidateAdmissionService:
    """把短期候选选择持久化为研究集合自己的书目条目。"""

    def __init__(
        self,
        session: CandidateReviewSession,
        bibliography: CollectionBibliographyRepository,
        selection: CandidateSelectionService,
    ) -> None:
        self._session = session
        self._bibliography = bibliography
        self._selection = selection

    async def admit_selected(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> CandidateAdmissionBatchResponse:
        """把用户已选候选逐篇加入研究集合，不以题录或 PDF 状态阻塞。"""
        run = await self._session.owned_finished_run(owner_user_id, collection_id, search_run_id)
        selected_ids = await self._session.selected_ids(run)
        _snapshot, all_candidates = await self._session.snapshot_candidates(run)
        candidates = self._session.visible_candidates(all_candidates)
        selected_ids = await self._session.synchronize_visible_selection(
            run, selected_ids, candidates
        )
        if not selected_ids:
            await self._session.require_nonempty_selection(run)
        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        items: list[CandidateAdmissionItem] = []
        succeeded_ids: list[UUID] = []
        admitted_count = 0
        already_joined_count = 0

        for candidate_id in sorted(selected_ids, key=str):
            try:
                candidate = candidate_by_id[candidate_id]
                draft = self._draft_from_candidate(search_run_id=run.id, candidate=candidate)
                result = await self._bibliography.upsert_from_candidate(
                    owner_user_id=owner_user_id,
                    collection_id=collection_id,
                    draft=draft,
                )
                if result.status is CollectionBibliographyUpsertStatus.ADDED:
                    admitted_count += 1
                else:
                    already_joined_count += 1
                succeeded_ids.append(candidate_id)
                items.append(
                    CandidateAdmissionItem(
                        candidate_id=candidate_id,
                        status=result.status.value,
                        message=(
                            "已加入研究集合。"
                            if result.status is CollectionBibliographyUpsertStatus.ADDED
                            else "该文献已在当前集合中。"
                        ),
                    )
                )
            except CollectionBibliographyError:
                items.append(
                    CandidateAdmissionItem(
                        candidate_id=candidate_id,
                        status="blocked",
                        message="暂时无法加入研究集合。",
                        retryable=False,
                    )
                )

        if succeeded_ids:
            await self._selection.update_selection(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                search_run_id=search_run_id,
                candidate_ids=succeeded_ids,
                selected=False,
            )

        return CandidateAdmissionBatchResponse(
            run_id=run.id,
            selected_count=len(selected_ids),
            admitted_count=admitted_count,
            already_joined_count=already_joined_count,
            blocked_count=len(selected_ids) - len(succeeded_ids),
            items=items,
        )

    def _draft_from_candidate(
        self,
        *,
        search_run_id: UUID,
        candidate: UnifiedCandidate,
    ) -> CollectionBibliographyEntryDraft:
        citation_status, citation_text = self._citation_projection(
            search_run_id=search_run_id,
            candidate=candidate,
        )
        pdf_status = (
            BibliographyPdfStatus.AVAILABLE
            if candidate.pdf_availability is not None
            and candidate.pdf_availability.status is CandidatePdfAvailabilityStatus.AVAILABLE
            else BibliographyPdfStatus.REQUIRES_UPLOAD
        )
        return CollectionBibliographyEntryDraft(
            source_search_run_id=search_run_id,
            source_candidate_id=candidate.candidate_id,
            title=candidate.title,
            authors=tuple(author.model_dump(mode="json") for author in candidate.authors),
            abstract=candidate.abstract,
            publication_year=candidate.published_year,
            venue=candidate.venue,
            doi=candidate.doi,
            source_url=(
                candidate.links.landing_url
                or candidate.links.open_access_url
                or candidate.links.fulltext_url
            ),
            source_record=self._source_record_snapshot(candidate),
            citation_status=citation_status,
            citation_text=citation_text,
            citation_snapshot=(
                candidate.citation.model_dump(mode="json")
                if candidate.citation is not None
                else {"status": BibliographyCitationStatus.UNAVAILABLE.value}
            ),
            pdf_status=pdf_status,
            pdf_source_url=(
                candidate.links.fulltext_url
                if pdf_status is BibliographyPdfStatus.AVAILABLE
                else None
            ),
            pdf_snapshot=(
                candidate.pdf_availability.model_dump(mode="json")
                if candidate.pdf_availability is not None
                else {"status": BibliographyPdfStatus.REQUIRES_UPLOAD.value}
            ),
            content_status=(
                BibliographyContentStatus.PENDING_AUTO_DOWNLOAD
                if pdf_status is BibliographyPdfStatus.AVAILABLE
                else BibliographyContentStatus.REQUIRES_UPLOAD
            ),
        )

    @staticmethod
    def _source_record_snapshot(candidate: UnifiedCandidate) -> dict[str, Any]:
        return {
            "source_records": [
                source_record.model_dump(mode="json") for source_record in candidate.source_records
            ],
            "field_provenance": {
                field: source.value for field, source in candidate.field_provenance.items()
            },
            "conflicts": {field: list(values) for field, values in candidate.conflicts.items()},
        }

    def _citation_projection(
        self,
        *,
        search_run_id: UUID,
        candidate: UnifiedCandidate,
    ) -> tuple[BibliographyCitationStatus, str | None]:
        citation = candidate.citation
        if citation is None or citation.status is not CitationMetadataStatus.READY:
            return BibliographyCitationStatus.UNAVAILABLE, None
        try:
            return (
                BibliographyCitationStatus.READY,
                format_citation(citation, CitationFormat.GB_T_7714_2015_NUMERIC),
            )
        except CitationFormattingError:
            logger.info(
                "Candidate citation formatting unavailable during admission: "
                "run_id=%s candidate_id=%s",
                search_run_id,
                candidate.candidate_id,
            )
            return BibliographyCitationStatus.UNAVAILABLE, None
