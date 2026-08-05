"""候选审核中的批量文献准入用例。"""

from __future__ import annotations

from uuid import UUID

from app.modules.documents.api_contracts import CandidateFulltextError
from app.modules.documents.service import CandidateFulltextService
from app.modules.literature.admission import (
    CollectionAdmissionError,
    CollectionAdmissionStatus,
    LiteratureAdmissionCandidate,
    LiteratureAdmissionPort,
)
from app.modules.search.api_contracts import (
    CandidateAdmissionBatchResponse,
    CandidateAdmissionItem,
    SearchRunError,
)
from app.modules.search.review_selection import CandidateSelectionService
from app.modules.search.review_session import CandidateReviewSession


class CandidateAdmissionService:
    """逐篇调用文献准入端口，并只从清单移除成功项。"""

    def __init__(
        self,
        session: CandidateReviewSession,
        fulltext: CandidateFulltextService,
        admission: LiteratureAdmissionPort,
        selection: CandidateSelectionService,
    ) -> None:
        self._session = session
        self._fulltext = fulltext
        self._admission = admission
        self._selection = selection

    async def admit_selected(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> CandidateAdmissionBatchResponse:
        """把已具备可处理全文的准备候选逐篇纳入待确认集合。"""
        run = await self._session.owned_finished_run(owner_user_id, collection_id, search_run_id)
        selected_ids = await self._session.require_nonempty_selection(run)
        items: list[CandidateAdmissionItem] = []
        succeeded_ids: list[UUID] = []
        admitted_count = 0
        already_joined_count = 0

        for candidate_id in sorted(selected_ids, key=str):
            try:
                submission = await self._fulltext.get_state(
                    owner_user_id=owner_user_id,
                    collection_id=collection_id,
                    search_run_id=search_run_id,
                    candidate_id=candidate_id,
                )
                result = await self._admission.admit(
                    owner_user_id=owner_user_id,
                    collection_id=collection_id,
                    candidate=LiteratureAdmissionCandidate(
                        candidate_id=submission.state.candidate.candidate_id,
                        doi=submission.state.candidate.doi,
                        abstract=submission.state.candidate.abstract,
                        official_url=(
                            submission.state.candidate.links.landing_url
                            or submission.state.candidate.links.open_access_url
                        ),
                        citation=submission.state.candidate.citation,
                    ),
                    fulltext_result=submission.state.result,
                )
                if result.status is CollectionAdmissionStatus.ADDED:
                    admitted_count += 1
                else:
                    already_joined_count += 1
                succeeded_ids.append(candidate_id)
                items.append(
                    CandidateAdmissionItem(
                        candidate_id=candidate_id,
                        status=result.status.value,
                        message=(
                            "已加入待确认集合。"
                            if result.status is CollectionAdmissionStatus.ADDED
                            else "该文献已在当前集合中。"
                        ),
                    )
                )
            except (CandidateFulltextError, SearchRunError, CollectionAdmissionError) as exc:
                retryable = bool(getattr(exc, "retryable", False))
                items.append(
                    CandidateAdmissionItem(
                        candidate_id=candidate_id,
                        status="blocked",
                        message=str(exc),
                        retryable=retryable,
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
