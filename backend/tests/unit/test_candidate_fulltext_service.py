"""搜索候选全文任务的权限、幂等和重试离线测试。"""

from __future__ import annotations

from collections.abc import AsyncIterable
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from app.modules.documents.api_contracts import (
    CandidateFulltextError,
    CandidateFulltextErrorCode,
)
from app.modules.documents.contracts import (
    AcquiredFulltext,
    CandidateFulltextState,
    FulltextAcquisitionError,
    FulltextAcquisitionErrorCode,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
    FulltextCandidate,
)
from app.modules.documents.queue import CandidateFulltextQueueError
from app.modules.documents.service import CandidateFulltextService
from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateLinks,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.search.fulltext_candidate import (
    SearchCandidateFulltextLookup,
    to_fulltext_candidate,
)
from app.modules.search.run_models import SearchRunRecord
from app.modules.search.run_repository import SearchRunRepository
from app.modules.search.session import SearchSessionStore
from tests.unit.fakes_search_candidates import FakeSearchCandidateRepository

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000801")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000802")
_PLAN_ID = UUID("00000000-0000-0000-0000-000000000803")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000804")
_CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000805")
_SESSION_KEY = "academic-search:search-run:00000000-0000-0000-0000-000000000804"


class FakeSearchRunRepository:
    """只提供搜索运行所有权查询所需的标量返回值。"""

    def __init__(self, run: SearchRunRecord) -> None:
        self._run = run

    async def get_owned_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        for_update: bool = False,
    ) -> SearchRunRecord | None:
        del for_update
        if (
            owner_user_id != _OWNER_ID
            or collection_id != self._run.collection_id
            or search_run_id != self._run.id
        ):
            return None
        return self._run


class FakeSessionStore:
    """只模拟上传锁；候选和全文状态事实由持久仓储 fake 负责。"""

    def __init__(self) -> None:
        self.locks: dict[str, str] = {}

    async def try_acquire_lock(self, _key: str, *, token: str, ttl_seconds: int) -> bool:
        assert token
        assert ttl_seconds > 0
        if _key in self.locks:
            return False
        self.locks[_key] = token
        return True

    async def release_lock(self, _key: str, *, token: str) -> None:
        assert token
        if self.locks.get(_key) == token:
            self.locks.pop(_key)


class FakeQueue:
    """记录任务投递参数，并可模拟队列连接失败。"""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.calls: list[tuple[UUID, UUID, int]] = []

    async def enqueue_fulltext(
        self,
        *,
        search_run_id: UUID,
        candidate_id: UUID,
        attempt_no: int,
    ) -> str:
        if self._fail:
            raise CandidateFulltextQueueError("test queue unavailable")
        self.calls.append((search_run_id, candidate_id, attempt_no))
        return f"fulltext-{search_run_id}-{candidate_id}-{attempt_no}"


class FakeUploader:
    """避免服务层测试触碰对象存储，同时记录授权后的真实候选输入。"""

    def __init__(self) -> None:
        self.candidate_ids: list[UUID] = []

    async def acquire(
        self,
        *,
        candidate: FulltextCandidate,
        chunks: AsyncIterable[bytes],
        media_type: str | None,
    ) -> FulltextAcquisitionResult:
        self.candidate_ids.append(candidate.candidate_id)
        async for _chunk in chunks:
            pass
        return FulltextAcquisitionResult(
            candidate_id=candidate.candidate_id,
            status=FulltextAcquisitionStatus.AVAILABLE,
            document=AcquiredFulltext(
                candidate_id=candidate.candidate_id,
                doi=candidate.doi or "10.1000/fulltext.example",
                source_url="user-upload://candidate/test",
                staging_object_key="staging/fulltext/test.pdf",
                original_filename="authorized-upload.pdf",
                byte_size=24,
                sha256="0" * 64,
                origin_kind="user_upload",
                access_rights="user_upload",
                acquired_at=datetime.now(UTC),
            ),
        )


def _run() -> SearchRunRecord:
    """构造已完成且拥有持久候选事实的检索运行。"""
    now = datetime.now(UTC)
    return SearchRunRecord(
        id=_RUN_ID,
        collection_id=_COLLECTION_ID,
        research_plan_id=_PLAN_ID,
        arq_job_id=None,
        redis_session_key=_SESSION_KEY,
        status="completed",
        stage="completed",
        attempt_no=1,
        provider_summary={},
        candidate_counts={},
        error_code=None,
        error_message=None,
        started_at=now,
        finished_at=now,
        created_at=now,
        updated_at=now,
    )


def _candidate(*, included: bool = True) -> UnifiedCandidate:
    """构造当前检索会话内的一篇候选，不从客户端接收全文 URL。"""
    source_record = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id="W-fulltext-test",
        title="A candidate restricted to its search session",
        authors=(CandidateAuthor(name="Ada Lovelace"),),
        doi="10.1000/fulltext.example",
    )
    return UnifiedCandidate(
        candidate_id=_CANDIDATE_ID,
        doi="10.1000/fulltext.example",
        title=source_record.title,
        title_key="a candidate restricted to its search session",
        authors=source_record.authors,
        links=CandidateLinks(landing_url="https://doi.org/10.1000/fulltext.example"),
        source_records=(source_record,),
        triage=TriageDecision(included=included),
    )


def _service(
    candidates: FakeSearchCandidateRepository,
    *,
    store: FakeSessionStore | None = None,
    queue: FakeQueue | None = None,
    uploader: Any | None = None,
) -> CandidateFulltextService:
    """Compose the full-text use case with explicit Search-owned candidate lookup."""
    runs = cast(SearchRunRepository, FakeSearchRunRepository(_run()))
    session_store = store or FakeSessionStore()
    return CandidateFulltextService(
        runs,
        cast(SearchSessionStore, session_store),
        queue,
        candidate_lookup=SearchCandidateFulltextLookup(runs, candidates),
        state_store=candidates,
        uploader=uploader,
    )


def _candidate_repository(
    candidate: UnifiedCandidate,
    *,
    fulltext_states: tuple[CandidateFulltextState, ...] = (),
) -> FakeSearchCandidateRepository:
    """初始化持久候选事实，确保服务从服务端候选而非前端数据读取。"""
    return FakeSearchCandidateRepository(
        search_run_id=_RUN_ID,
        candidates=(candidate,),
        fulltext_states=fulltext_states,
    )


@pytest.mark.asyncio
async def test_request_uses_server_candidate_and_repeated_click_is_idempotent() -> None:
    """同一候选第二次请求应返回已有 queued 状态，而不是再次投递下载任务。"""
    candidate = _candidate()
    candidates = _candidate_repository(candidate)
    queue = FakeQueue()
    service = _service(candidates, queue=queue)

    first = await service.request(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_id=_CANDIDATE_ID,
    )
    second = await service.request(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_id=_CANDIDATE_ID,
    )

    assert first.state.result.status is FulltextAcquisitionStatus.QUEUED
    assert first.state.arq_job_id == f"fulltext-{_RUN_ID}-{_CANDIDATE_ID}-1"
    assert second.state == first.state
    assert queue.calls == [(_RUN_ID, _CANDIDATE_ID, 1)]


@pytest.mark.asyncio
async def test_request_rejects_a_candidate_excluded_by_server_side_triage() -> None:
    """前端知道 UUID 也不能绕过候选初筛来获取全文。"""
    candidates = _candidate_repository(_candidate(included=False))
    queue = FakeQueue()
    service = _service(candidates, queue=queue)

    with pytest.raises(CandidateFulltextError) as raised:
        await service.request(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            search_run_id=_RUN_ID,
            candidate_id=_CANDIDATE_ID,
        )

    assert raised.value.code is CandidateFulltextErrorCode.CANDIDATE_NOT_ELIGIBLE
    assert queue.calls == []


@pytest.mark.asyncio
async def test_retry_creates_a_new_attempt_only_for_retryable_terminal_failure() -> None:
    """重试保留第一轮失败状态，并以新的 attempt_no 形成新的 arq 任务标识。"""
    candidate = _candidate()
    now = datetime.now(UTC)
    failed_state = CandidateFulltextState(
        search_run_id=_RUN_ID,
        candidate=to_fulltext_candidate(candidate),
        attempt_no=1,
        result=FulltextAcquisitionResult(
            candidate_id=_CANDIDATE_ID,
            status=FulltextAcquisitionStatus.FAILED,
            error=FulltextAcquisitionError(
                code=FulltextAcquisitionErrorCode.NETWORK_ERROR,
                message="network interrupted",
                retryable=True,
            ),
        ),
        requested_at=now,
        updated_at=now,
    )
    candidates = _candidate_repository(candidate, fulltext_states=(failed_state,))
    queue = FakeQueue()
    service = _service(candidates, queue=queue)

    submission = await service.request(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_id=_CANDIDATE_ID,
        retry=True,
    )

    assert submission.state.attempt_no == 2
    assert submission.state.result.status is FulltextAcquisitionStatus.QUEUED
    assert submission.state.arq_job_id == f"fulltext-{_RUN_ID}-{_CANDIDATE_ID}-2"
    assert queue.calls == [(_RUN_ID, _CANDIDATE_ID, 2)]


@pytest.mark.asyncio
async def test_queue_failure_is_returned_as_retryable_failed_state() -> None:
    """队列不可用不能留下 queued 假象，前端应得到明确的可重试失败。"""
    candidate = _candidate()
    service = _service(_candidate_repository(candidate), queue=FakeQueue(fail=True))

    submission = await service.request(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_id=_CANDIDATE_ID,
    )

    assert submission.state.result.status is FulltextAcquisitionStatus.FAILED
    assert submission.state.result.error is not None
    assert submission.state.result.error.code is FulltextAcquisitionErrorCode.TASK_ERROR
    assert submission.state.result.error.retryable is True


@pytest.mark.asyncio
async def test_upload_requires_an_explicit_authorization_statement() -> None:
    """客户端知道候选 UUID 也不能在未确认权限时写入私有暂存区。"""
    candidate = _candidate()
    uploader = FakeUploader()
    service = _service(_candidate_repository(candidate), uploader=cast(Any, uploader))

    async def chunks() -> AsyncIterable[bytes]:
        yield b"%PDF-1.7\n"

    with pytest.raises(CandidateFulltextError) as raised:
        await service.upload(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            search_run_id=_RUN_ID,
            candidate_id=_CANDIDATE_ID,
            authorized_to_process=False,
            chunks=chunks(),
            media_type="application/pdf",
        )

    assert raised.value.code is CandidateFulltextErrorCode.UPLOAD_NOT_AUTHORIZED
    assert uploader.candidate_ids == []


@pytest.mark.asyncio
async def test_upload_uses_the_server_side_candidate_and_writes_available_state() -> None:
    """上传只消费服务端快照中的候选，成功后沿用既有可准入全文状态。"""
    candidate = _candidate()
    uploader = FakeUploader()
    service = _service(_candidate_repository(candidate), uploader=cast(Any, uploader))

    async def chunks() -> AsyncIterable[bytes]:
        yield b"%PDF-1.7\n"

    submission = await service.upload(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_id=_CANDIDATE_ID,
        authorized_to_process=True,
        chunks=chunks(),
        media_type="application/pdf",
    )

    assert submission.state.attempt_no == 1
    assert submission.state.result.status is FulltextAcquisitionStatus.AVAILABLE
    assert uploader.candidate_ids == [_CANDIDATE_ID]
