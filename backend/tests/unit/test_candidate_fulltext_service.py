"""搜索候选全文任务的权限、幂等和重试离线测试。"""

from __future__ import annotations

from collections.abc import AsyncIterable
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models.workflow import SearchRun
from app.modules.fulltext.contracts import (
    AcquiredFulltext,
    CandidateFulltextState,
    FulltextAcquisitionError,
    FulltextAcquisitionErrorCode,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
)
from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateLinks,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.workflow.contracts import CandidateFulltextError, CandidateFulltextErrorCode
from app.modules.workflow.fulltext_service import CandidateFulltextService
from app.modules.workflow.job_queue import CandidateFulltextQueueError
from app.modules.workflow.search_session import SearchSessionStore, build_candidate_fulltext_key
from sqlalchemy.ext.asyncio import AsyncSession

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000801")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000802")
_PLAN_ID = UUID("00000000-0000-0000-0000-000000000803")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000804")
_CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000805")
_SESSION_KEY = "academic-search:search-run:00000000-0000-0000-0000-000000000804"


class FakeSession:
    """只提供搜索运行所有权查询所需的标量返回值。"""

    def __init__(self, run: SearchRun) -> None:
        self._run = run

    async def scalar(self, _statement: object) -> SearchRun:
        return self._run


class FakeSessionStore:
    """使用内存字典代替 Redis，保留真实服务的读写边界。"""

    def __init__(self, snapshots: dict[str, dict[str, Any]]) -> None:
        self.snapshots = snapshots
        self.writes: list[tuple[str, dict[str, Any]]] = []

    async def read_snapshot(self, session_key: str) -> dict[str, Any] | None:
        return self.snapshots.get(session_key)

    async def write_snapshot(self, session_key: str, snapshot: dict[str, Any]) -> None:
        self.snapshots[session_key] = snapshot
        self.writes.append((session_key, snapshot))

    async def try_acquire_lock(self, _key: str, *, token: str, ttl_seconds: int) -> bool:
        assert token
        assert ttl_seconds > 0
        return True

    async def release_lock(self, _key: str, *, token: str) -> None:
        assert token


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
        candidate: UnifiedCandidate,
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


def _run() -> SearchRun:
    """构造已完成且拥有 Redis 候选会话的检索运行。"""
    return SearchRun(
        id=_RUN_ID,
        collection_id=_COLLECTION_ID,
        research_plan_id=_PLAN_ID,
        redis_session_key=_SESSION_KEY,
        status="completed",
        stage="completed",
        attempt_no=1,
        provider_summary={},
        candidate_counts={},
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


def _store(candidate: UnifiedCandidate) -> FakeSessionStore:
    """初始化真实搜索快照形状，确保服务从服务端候选而非前端数据读取。"""
    return FakeSessionStore(
        {
            _SESSION_KEY: {
                "candidates": [candidate.model_dump(mode="json")],
            }
        }
    )


@pytest.mark.asyncio
async def test_request_uses_server_candidate_and_repeated_click_is_idempotent() -> None:
    """同一候选第二次请求应返回已有 queued 状态，而不是再次投递下载任务。"""
    candidate = _candidate()
    store = _store(candidate)
    queue = FakeQueue()
    service = CandidateFulltextService(
        cast(AsyncSession, FakeSession(_run())),
        cast(SearchSessionStore, store),
        queue,
    )

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
    store = _store(_candidate(included=False))
    queue = FakeQueue()
    service = CandidateFulltextService(
        cast(AsyncSession, FakeSession(_run())),
        cast(SearchSessionStore, store),
        queue,
    )

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
    store = _store(candidate)
    state_key = build_candidate_fulltext_key(_SESSION_KEY, _CANDIDATE_ID)
    now = datetime.now(UTC)
    store.snapshots[state_key] = CandidateFulltextState(
        search_run_id=_RUN_ID,
        candidate=candidate,
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
    ).model_dump(mode="json")
    queue = FakeQueue()
    service = CandidateFulltextService(
        cast(AsyncSession, FakeSession(_run())),
        cast(SearchSessionStore, store),
        queue,
    )

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
    service = CandidateFulltextService(
        cast(AsyncSession, FakeSession(_run())),
        cast(SearchSessionStore, _store(candidate)),
        FakeQueue(fail=True),
    )

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
    service = CandidateFulltextService(
        cast(AsyncSession, FakeSession(_run())),
        cast(SearchSessionStore, _store(candidate)),
        uploader=cast(Any, uploader),
    )

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
    store = _store(candidate)
    uploader = FakeUploader()
    service = CandidateFulltextService(
        cast(AsyncSession, FakeSession(_run())),
        cast(SearchSessionStore, store),
        uploader=cast(Any, uploader),
    )

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
