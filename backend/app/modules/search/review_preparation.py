"""候选审核中的批量全文准备用例。"""

from __future__ import annotations

from uuid import UUID

from app.modules.documents.api_contracts import CandidateFulltextError
from app.modules.documents.contracts import FulltextAcquisitionStatus
from app.modules.documents.service import CandidateFulltextService
from app.modules.search.api_contracts import (
    CandidatePreparationBatchResponse,
    CandidatePreparationItem,
    SearchRunError,
)
from app.modules.search.review_session import CandidateReviewSession


class CandidatePreparationService:
    """为准备清单逐篇调用全文用例，不复制全文业务规则。"""

    def __init__(
        self,
        session: CandidateReviewSession,
        fulltext: CandidateFulltextService,
    ) -> None:
        self._session = session
        self._fulltext = fulltext

    async def prepare_selected(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
    ) -> CandidatePreparationBatchResponse:
        """为准备清单逐篇投递既有全文任务，题录补齐由全文 Worker 负责。"""
        run = await self._session.owned_finished_run(owner_user_id, collection_id, search_run_id)
        selected_ids = await self._session.require_nonempty_selection(run)
        items: list[CandidatePreparationItem] = []
        queued_count = 0

        for candidate_id in sorted(selected_ids, key=str):
            try:
                submission = await self._fulltext.request(
                    owner_user_id=owner_user_id,
                    collection_id=collection_id,
                    search_run_id=search_run_id,
                    candidate_id=candidate_id,
                    retry=True,
                )
                result = submission.state.result
                if result.status in {
                    FulltextAcquisitionStatus.QUEUED,
                    FulltextAcquisitionStatus.DOWNLOADING,
                    FulltextAcquisitionStatus.VALIDATING,
                }:
                    queued_count += 1
                items.append(
                    CandidatePreparationItem(
                        candidate_id=candidate_id,
                        status=result.status,
                        message=self._fulltext_message(
                            result.status,
                            result.error.message if result.error else None,
                        ),
                        retryable=bool(result.error and result.error.retryable),
                    )
                )
            except (CandidateFulltextError, SearchRunError) as exc:
                items.append(
                    CandidatePreparationItem(
                        candidate_id=candidate_id,
                        message=str(exc),
                        retryable=False,
                    )
                )

        return CandidatePreparationBatchResponse(
            run_id=run.id,
            selected_count=len(selected_ids),
            queued_count=queued_count,
            items=items,
        )

    @staticmethod
    def _fulltext_message(status: FulltextAcquisitionStatus, error_message: str | None) -> str:
        """批量操作只承诺实际状态，不把任务投递说成全文已经可用。"""
        if error_message:
            return error_message
        if status is FulltextAcquisitionStatus.AVAILABLE:
            return "全文已核验，可加入待确认集合。"
        if status in {
            FulltextAcquisitionStatus.QUEUED,
            FulltextAcquisitionStatus.DOWNLOADING,
            FulltextAcquisitionStatus.VALIDATING,
        }:
            return "题录与全文核验已安排，结果会在本页更新。"
        return "全文暂时无法用于研究集合。"
