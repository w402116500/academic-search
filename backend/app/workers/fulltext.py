"""候选全文获取的 arq Worker。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.settings import get_literature_source_settings
from app.db.models.workflow import SearchRun
from app.db.session import async_session_factory
from app.modules.fulltext import (
    Boto3StagingObjectStorage,
    CandidateFulltextState,
    FulltextAcquisitionError,
    FulltextAcquisitionErrorCode,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
    OpenAccessPdfAcquirer,
    get_fulltext_acquisition_settings,
)
from app.modules.search.citation_enrichment import CitationMetadataEnricher
from app.modules.search.contracts import CitationMetadataStatus
from app.modules.search.providers.doi_resolver import DoiMetadataResolver
from app.modules.workflow.search_session import (
    SearchSessionStore,
    build_candidate_fulltext_key,
)
from app.workers.redis import redis_client_from_environment


async def acquire_candidate_fulltext(
    _ctx: dict[str, Any],
    search_run_id: str,
    candidate_id: str,
    attempt_no: int,
) -> dict[str, str]:
    """补齐题录、获取开放全文并写入候选专属 Redis 状态。"""
    try:
        run_id = UUID(search_run_id)
        parsed_candidate_id = UUID(candidate_id)
    except ValueError as exc:
        raise ValueError("arq 全文任务缺少合法的检索运行或候选标识。") from exc
    if attempt_no < 1:
        raise ValueError("arq 全文任务的尝试序号必须从 1 开始。")

    async with async_session_factory() as session:
        run = await session.get(SearchRun, run_id)
        if run is None or run.redis_session_key is None:
            return {"search_run_id": str(run_id), "status": "ignored"}

        redis = redis_client_from_environment()
        state: CandidateFulltextState | None = None
        store: SearchSessionStore | None = None
        state_key = build_candidate_fulltext_key(run.redis_session_key, parsed_candidate_id)
        try:
            settings = get_fulltext_acquisition_settings()
            source_settings = get_literature_source_settings()
            store = SearchSessionStore(
                redis, ttl_seconds=source_settings.search_session_ttl_seconds
            )
            raw_state = await store.read_snapshot(state_key)
            if raw_state is None:
                return {"search_run_id": str(run_id), "status": "ignored"}
            state = CandidateFulltextState.model_validate(raw_state)
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
            await store.write_snapshot(state_key, downloading.model_dump(mode="json"))

            candidate = downloading.candidate
            if (
                candidate.citation is None
                or candidate.citation.status is not CitationMetadataStatus.READY
            ):
                citation_settings = get_literature_source_settings()
                candidate = await CitationMetadataEnricher(
                    DoiMetadataResolver(citation_settings.doi_resolver)
                ).enrich(candidate)

            validating = downloading.model_copy(
                update={
                    "candidate": candidate,
                    "result": FulltextAcquisitionResult(
                        candidate_id=candidate.candidate_id,
                        status=FulltextAcquisitionStatus.VALIDATING,
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
            await store.write_snapshot(state_key, validating.model_dump(mode="json"))

            acquisition = await OpenAccessPdfAcquirer(
                settings,
                Boto3StagingObjectStorage(settings),
            ).acquire(candidate)
            completed = validating.model_copy(
                update={"result": acquisition, "updated_at": datetime.now(UTC)}
            )
            await store.write_snapshot(state_key, completed.model_dump(mode="json"))
            return {
                "search_run_id": str(run_id),
                "candidate_id": str(parsed_candidate_id),
                "status": acquisition.status.value,
            }
        except Exception:
            failed = _task_failed_state(state)
            if failed is not None and store is not None:
                await store.write_snapshot(state_key, failed.model_dump(mode="json"))
            raise
        finally:
            await redis.aclose()


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
