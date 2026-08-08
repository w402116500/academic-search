"""候选全文获取的 arq Worker。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.fulltext_settings import get_fulltext_acquisition_settings
from app.core.settings import get_literature_source_settings
from app.infra.db.models.workflow import SearchRun
from app.infra.db.repositories.search_candidates import SqlAlchemySearchCandidateRepository
from app.infra.db.session import async_session_factory
from app.infra.storage.documents import Boto3StagingObjectStorage
from app.modules.documents.acquisition import OpenAccessPdfAcquirer
from app.modules.documents.contracts import (
    CandidateFulltextState,
    FulltextAcquisitionError,
    FulltextAcquisitionErrorCode,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
)
from app.modules.literature.contracts import CitationMetadataStatus
from app.modules.search.candidate_repository import SearchCandidateRepository
from app.modules.search.citation_enrichment import CitationMetadataEnricher
from app.modules.search.contracts import UnifiedCandidate
from app.modules.search.fulltext_candidate import to_fulltext_candidate
from app.modules.search.providers.doi_resolver import DoiMetadataResolver


async def acquire_candidate_fulltext(
    _ctx: dict[str, Any],
    search_run_id: str,
    candidate_id: str,
    attempt_no: int,
) -> dict[str, str]:
    """补齐题录、获取开放全文并写入持久候选全文状态。"""
    try:
        run_id = UUID(search_run_id)
        parsed_candidate_id = UUID(candidate_id)
    except ValueError as exc:
        raise ValueError("arq 全文任务缺少合法的检索运行或候选标识。") from exc
    if attempt_no < 1:
        raise ValueError("arq 全文任务的尝试序号必须从 1 开始。")

    async with async_session_factory() as session:
        run = await session.get(SearchRun, run_id)
        if run is None:
            return {"search_run_id": str(run_id), "status": "ignored"}

        candidates = SqlAlchemySearchCandidateRepository(session)
        state: CandidateFulltextState | None = None
        try:
            settings = get_fulltext_acquisition_settings()
            state = await candidates.get_fulltext_state(
                search_run_id=run_id,
                candidate_id=parsed_candidate_id,
            )
            if state is None:
                return {"search_run_id": str(run_id), "status": "ignored"}
            if (
                state.attempt_no != attempt_no
                or state.result.status is not FulltextAcquisitionStatus.QUEUED
            ):
                return {"search_run_id": str(run_id), "status": "ignored"}

            downloading = state.model_copy(
                update={
                    "result": FulltextAcquisitionResult(
                        candidate_id=state.candidate.candidate_id,
                        status=FulltextAcquisitionStatus.DOWNLOADING,
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
            await candidates.write_fulltext_state(downloading)
            state = downloading

            candidate = await _load_search_candidate(
                candidates,
                run_id,
                parsed_candidate_id,
            )
            if candidate.citation is None and downloading.candidate.citation is not None:
                candidate = candidate.model_copy(
                    update={"citation": downloading.candidate.citation}
                )
            if (
                candidate.citation is None
                or candidate.citation.status is not CitationMetadataStatus.READY
            ):
                citation_settings = get_literature_source_settings()
                candidate = await CitationMetadataEnricher(
                    DoiMetadataResolver(citation_settings.doi_resolver)
                ).enrich(candidate)
                await candidates.update_readiness(search_run_id=run_id, candidates=(candidate,))

            validating = downloading.model_copy(
                update={
                    "candidate": to_fulltext_candidate(candidate),
                    "result": FulltextAcquisitionResult(
                        candidate_id=candidate.candidate_id,
                        status=FulltextAcquisitionStatus.VALIDATING,
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
            await candidates.write_fulltext_state(validating)
            state = validating

            acquisition = await OpenAccessPdfAcquirer(
                settings,
                Boto3StagingObjectStorage(settings),
            ).acquire(to_fulltext_candidate(candidate))
            completed = validating.model_copy(
                update={"result": acquisition, "updated_at": datetime.now(UTC)}
            )
            await candidates.write_fulltext_state(completed)
            return {
                "search_run_id": str(run_id),
                "candidate_id": str(parsed_candidate_id),
                "status": acquisition.status.value,
            }
        except Exception:
            failed = _task_failed_state(state)
            if failed is not None:
                await candidates.write_fulltext_state(failed)
            raise


def _task_failed_state(state: CandidateFulltextState | None) -> CandidateFulltextState | None:
    """把 Worker 非预期异常转为可重试的候选状态，避免前端永久显示下载中。"""
    if state is None:
        return None
    return state.model_copy(
        update={
            "result": FulltextAcquisitionResult(
                candidate_id=state.candidate.candidate_id,
                status=FulltextAcquisitionStatus.FAILED,
                error=FulltextAcquisitionError(
                    code=FulltextAcquisitionErrorCode.TASK_ERROR,
                    message="全文任务发生未预期错误，请稍后重试。",
                    retryable=True,
                ),
            ),
            "updated_at": datetime.now(UTC),
        }
    )


async def _load_search_candidate(
    candidates: SearchCandidateRepository,
    search_run_id: UUID,
    candidate_id: UUID,
) -> UnifiedCandidate:
    """读取持久 Search 候选事实，再投影回 Documents 全文边界。"""
    candidate = await candidates.get_candidate(
        search_run_id=search_run_id,
        candidate_id=candidate_id,
    )
    if candidate is None:
        raise ValueError("全文任务引用的候选不在当前检索运行中。")
    return candidate
