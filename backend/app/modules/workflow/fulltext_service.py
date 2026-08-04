"""基于搜索候选快照的异步全文任务服务。"""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.db.models.workflow import SearchRun
from app.modules.fulltext.acquisition import AuthorizedPdfUploader
from app.modules.fulltext.contracts import (
    CandidateFulltextState,
    FulltextAcquisitionError,
    FulltextAcquisitionErrorCode,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
)
from app.modules.search.contracts import UnifiedCandidate
from app.modules.workflow.candidate_lookup import (
    SearchCandidateLookupError,
    SearchCandidateLookupErrorCode,
    SearchCandidateLookupService,
)
from app.modules.workflow.contracts import (
    CandidateFulltextError,
    CandidateFulltextErrorCode,
)
from app.modules.workflow.job_queue import (
    CandidateFulltextJobQueue,
    CandidateFulltextQueueError,
)
from app.modules.workflow.search_run_service import SearchRunService
from app.modules.workflow.search_session import (
    SearchSessionStore,
    build_candidate_fulltext_key,
    build_candidate_fulltext_upload_lock_key,
)
from app.modules.workflow.state import SearchRunStatus
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class CandidateFulltextSubmission:
    """全文任务创建或读取后返回的搜索运行与短期状态。"""

    search_run: SearchRun
    state: CandidateFulltextState


class CandidateFulltextService:
    """只允许当前检索运行内的候选创建全文任务。"""

    def __init__(
        self,
        session: AsyncSession,
        session_store: SearchSessionStore,
        queue: CandidateFulltextJobQueue | None = None,
        *,
        uploader: AuthorizedPdfUploader | None = None,
    ) -> None:
        """注入请求范围的数据库、Redis 会话和可替换队列适配器。"""
        self._session = session
        self._session_store = session_store
        self._queue = queue
        self._uploader = uploader

    async def request(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        candidate_id: UUID,
        retry: bool = False,
    ) -> CandidateFulltextSubmission:
        """为合法候选创建全文获取任务；重复点击返回同一活跃任务。"""
        run = await SearchRunService(self._session).get_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
        self._require_finished_search(run)
        candidate = await self._load_candidate(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            run=run,
            candidate_id=candidate_id,
        )
        state_key = self._state_key(run, candidate_id)
        current = await self._read_state(state_key)

        if current is not None:
            active_statuses = {
                FulltextAcquisitionStatus.QUEUED,
                FulltextAcquisitionStatus.DOWNLOADING,
                FulltextAcquisitionStatus.VALIDATING,
            }
            if (
                current.result.status in active_statuses
                or current.result.status is FulltextAcquisitionStatus.AVAILABLE
            ):
                return CandidateFulltextSubmission(search_run=run, state=current)
            if not retry:
                return CandidateFulltextSubmission(search_run=run, state=current)
            if current.result.error is None or not current.result.error.retryable:
                raise CandidateFulltextError(
                    CandidateFulltextErrorCode.NOT_RETRYABLE,
                    "该全文获取结果不支持重试，请上传有权处理的 PDF 或选择其他文献。",
                )
            attempt_no = current.attempt_no + 1
        else:
            attempt_no = 1

        # 先验证基础设施依赖，再写入 queued 状态，避免留下没有对应 arq Job 的短期记录。
        if self._queue is None:
            raise RuntimeError("创建全文任务时必须提供任务队列")

        now = datetime.now(UTC)
        state = CandidateFulltextState(
            search_run_id=run.id,
            candidate=candidate,
            attempt_no=attempt_no,
            result=FulltextAcquisitionResult(
                candidate_id=candidate.candidate_id,
                status=FulltextAcquisitionStatus.QUEUED,
            ),
            requested_at=now,
            updated_at=now,
        )
        await self._write_state(state_key, state)

        try:
            job_id = await self._queue.enqueue_fulltext(
                search_run_id=run.id,
                candidate_id=candidate.candidate_id,
                attempt_no=attempt_no,
            )
        except CandidateFulltextQueueError:
            failed = state.model_copy(
                update={
                    "result": FulltextAcquisitionResult(
                        candidate_id=candidate.candidate_id,
                        status=FulltextAcquisitionStatus.FAILED,
                        error=FulltextAcquisitionError(
                            code=FulltextAcquisitionErrorCode.TASK_ERROR,
                            message="全文获取任务无法投递，请稍后重试。",
                            retryable=True,
                        ),
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
            await self._write_state(state_key, failed)
            return CandidateFulltextSubmission(search_run=run, state=failed)

        queued = state.model_copy(update={"arq_job_id": job_id, "updated_at": datetime.now(UTC)})
        await self._write_state(state_key, queued)
        return CandidateFulltextSubmission(search_run=run, state=queued)

    async def get_state(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        candidate_id: UUID,
    ) -> CandidateFulltextSubmission:
        """读取候选全文状态，并先验证候选仍属于当前用户的检索运行。"""
        run = await SearchRunService(self._session).get_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
        await self._load_candidate(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            run=run,
            candidate_id=candidate_id,
        )
        state = await self._read_state(self._state_key(run, candidate_id))
        if state is None:
            raise CandidateFulltextError(
                CandidateFulltextErrorCode.STATE_NOT_FOUND,
                "该候选尚未请求全文，或全文会话已过期。",
            )
        return CandidateFulltextSubmission(search_run=run, state=state)

    async def upload(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        candidate_id: UUID,
        authorized_to_process: bool,
        chunks: AsyncIterable[bytes],
        media_type: str | None,
    ) -> CandidateFulltextSubmission:
        """暂存当前用户明确授权的候选 PDF，并写回既有全文状态。"""
        if self._uploader is None:
            raise RuntimeError("上传候选 PDF 时必须提供暂存校验器")

        run = await SearchRunService(self._session).get_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            search_run_id=search_run_id,
        )
        self._require_finished_search(run)
        candidate = await self._load_candidate(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            run=run,
            candidate_id=candidate_id,
        )
        if not authorized_to_process:
            raise CandidateFulltextError(
                CandidateFulltextErrorCode.UPLOAD_NOT_AUTHORIZED,
                "请先明确确认你有权处理并上传这篇候选的 PDF。",
            )

        state_key = self._state_key(run, candidate_id)
        session_key = self._session_key(run)
        lock_key = build_candidate_fulltext_upload_lock_key(session_key, candidate_id)
        lock_token = uuid4().hex
        if not await self._session_store.try_acquire_lock(
            lock_key,
            token=lock_token,
            ttl_seconds=600,
        ):
            raise CandidateFulltextError(
                CandidateFulltextErrorCode.UPLOAD_IN_PROGRESS,
                "该候选的 PDF 正在上传或校验，请等待当前结果。",
            )
        try:
            current = await self._read_state(state_key)
            if current is not None:
                if current.result.status is FulltextAcquisitionStatus.AVAILABLE:
                    return CandidateFulltextSubmission(search_run=run, state=current)
                if current.result.status in {
                    FulltextAcquisitionStatus.QUEUED,
                    FulltextAcquisitionStatus.DOWNLOADING,
                    FulltextAcquisitionStatus.VALIDATING,
                }:
                    raise CandidateFulltextError(
                        CandidateFulltextErrorCode.UPLOAD_IN_PROGRESS,
                        "该候选正在进行全文处理，当前不能覆盖其结果。",
                    )
                attempt_no = current.attempt_no + 1
            else:
                attempt_no = 1

            now = datetime.now(UTC)
            validating = CandidateFulltextState(
                search_run_id=run.id,
                candidate=candidate,
                attempt_no=attempt_no,
                result=FulltextAcquisitionResult(
                    candidate_id=candidate.candidate_id,
                    status=FulltextAcquisitionStatus.VALIDATING,
                ),
                requested_at=now,
                updated_at=now,
            )
            await self._write_state(state_key, validating)
            result = await self._uploader.acquire(
                candidate=candidate,
                chunks=chunks,
                media_type=media_type,
            )
            completed = validating.model_copy(
                update={"result": result, "updated_at": datetime.now(UTC)}
            )
            await self._write_state(state_key, completed)
            return CandidateFulltextSubmission(search_run=run, state=completed)
        finally:
            await self._session_store.release_lock(lock_key, token=lock_token)

    async def _load_candidate(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        run: SearchRun,
        candidate_id: UUID,
    ) -> UnifiedCandidate:
        """复用候选读取边界，并保留全文流程对外稳定的错误码。"""
        try:
            lookup = await SearchCandidateLookupService(self._session, self._session_store).get(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                search_run_id=run.id,
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
        return lookup.candidate

    @staticmethod
    def _require_finished_search(run: SearchRun) -> None:
        """只有完整或部分成功的检索才有稳定候选可进入全文阶段。"""
        if run.status not in {
            SearchRunStatus.COMPLETED.value,
            SearchRunStatus.PARTIAL_FAILED.value,
        }:
            raise CandidateFulltextError(
                CandidateFulltextErrorCode.SEARCH_NOT_FINISHED,
                "文献检索尚未完成，暂时不能获取候选全文。",
            )

    @staticmethod
    def _state_key(run: SearchRun, candidate_id: UUID) -> str:
        """全文状态键由服务器持久化的会话键和候选 UUID 共同构成。"""
        if run.redis_session_key is None:
            raise CandidateFulltextError(
                CandidateFulltextErrorCode.SESSION_EXPIRED,
                "检索候选会话不存在，请重新执行文献检索。",
            )
        return build_candidate_fulltext_key(run.redis_session_key, candidate_id)

    @staticmethod
    def _session_key(run: SearchRun) -> str:
        """上传锁也只能使用持久化运行提供的服务器端 Redis 会话键。"""
        if run.redis_session_key is None:
            raise CandidateFulltextError(
                CandidateFulltextErrorCode.SESSION_EXPIRED,
                "检索候选会话不存在，请重新执行文献检索。",
            )
        return run.redis_session_key

    async def _read_state(self, state_key: str) -> CandidateFulltextState | None:
        """读取并校验 Redis 中的短期全文状态。"""
        raw_state = await self._session_store.read_snapshot(state_key)
        return CandidateFulltextState.model_validate(raw_state) if raw_state is not None else None

    async def _write_state(self, state_key: str, state: CandidateFulltextState) -> None:
        """保存全文任务状态，并与搜索候选会话使用相同 TTL。"""
        await self._session_store.write_snapshot(state_key, state.model_dump(mode="json"))
