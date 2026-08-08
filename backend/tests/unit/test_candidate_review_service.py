"""候选审核分页、准备清单和批量全文投递的离线测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from app.modules.documents.contracts import (
    CandidateFulltextState,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
)
from app.modules.documents.keys import build_candidate_fulltext_key
from app.modules.documents.service import CandidateFulltextService
from app.modules.literature.contracts import (
    CitationAuthor as LiteratureCitationAuthor,
)
from app.modules.literature.contracts import (
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
)
from app.modules.research.bibliography import (
    BibliographyCitationStatus,
    BibliographyContentStatus,
    BibliographyPdfStatus,
    CollectionBibliographyEntryDraft,
    CollectionBibliographyEntryResult,
    CollectionBibliographyError,
    CollectionBibliographyErrorCode,
    CollectionBibliographyRepository,
    CollectionBibliographyUpsertStatus,
)
from app.modules.search.api_contracts import CandidateReviewFilter
from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateLinks,
    CandidatePdfAvailability,
    CandidatePdfAvailabilityStatus,
    CandidateRelevanceAssessment,
    CandidateRelevanceError,
    CandidateRelevanceEvidence,
    CandidateRelevanceLevel,
    CandidateRelevanceState,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.search.fulltext_candidate import (
    SearchCandidateFulltextLookup,
    to_fulltext_candidate,
)
from app.modules.search.review_admission import CandidateAdmissionService
from app.modules.search.review_preparation import CandidatePreparationService
from app.modules.search.review_query import CandidateReviewQueryService
from app.modules.search.review_selection import CandidateSelectionService
from app.modules.search.review_session import (
    CandidateReviewError,
    CandidateReviewErrorCode,
    CandidateReviewSession,
)
from app.modules.search.run_models import SearchRunRecord
from app.modules.search.run_repository import SearchRunRepository
from app.modules.search.session import SearchSessionStore

_OWNER_ID = UUID("00000000-0000-0000-0000-000000001201")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000001202")
_PLAN_ID = UUID("00000000-0000-0000-0000-000000001203")
_RUN_ID = UUID("00000000-0000-0000-0000-000000001204")
_FIRST_ID = UUID("00000000-0000-0000-0000-000000001205")
_SECOND_ID = UUID("00000000-0000-0000-0000-000000001206")
_SESSION_KEY = "academic-search:search-run:00000000-0000-0000-0000-000000001204"


class FakeSearchRunRepository:
    """返回当前用户拥有的运行领域快照。"""

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
    """保留 Redis 快照、锁和 TTL 语义的内存替身。"""

    def __init__(self, snapshots: dict[str, dict[str, Any]]) -> None:
        self.snapshots = snapshots
        self.locks: dict[str, str] = {}
        self.refreshed_keys: list[str] = []

    async def read_snapshot(self, key: str) -> dict[str, Any] | None:
        return self.snapshots.get(key)

    async def read_many_snapshots(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        return {key: self.snapshots[key] for key in keys if key in self.snapshots}

    async def write_snapshot(self, key: str, value: dict[str, Any]) -> None:
        self.snapshots[key] = value

    async def refresh_ttl(self, key: str) -> None:
        self.refreshed_keys.append(key)

    async def try_acquire_lock(self, key: str, *, token: str, ttl_seconds: int) -> bool:
        _ = ttl_seconds
        if key in self.locks:
            return False
        self.locks[key] = token
        return True

    async def release_lock(self, key: str, *, token: str) -> None:
        if self.locks.get(key) == token:
            self.locks.pop(key)


class FakeQueue:
    """记录批量准备最终复用的单篇全文任务投递。"""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID, int]] = []

    async def enqueue_fulltext(
        self,
        *,
        search_run_id: UUID,
        candidate_id: UUID,
        attempt_no: int,
    ) -> str:
        self.calls.append((search_run_id, candidate_id, attempt_no))
        return f"fulltext-{search_run_id}-{candidate_id}-{attempt_no}"


class FakeBibliographyRepository:
    """记录集合书目 upsert 请求，不创建 Paper 或 Document。"""

    def __init__(self, *, fail_candidate_ids: set[UUID] | None = None) -> None:
        self.drafts: list[CollectionBibliographyEntryDraft] = []
        self._seen_candidate_ids: set[UUID] = set()
        self._fail_candidate_ids = fail_candidate_ids or set()

    async def upsert_from_candidate(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        draft: CollectionBibliographyEntryDraft,
    ) -> CollectionBibliographyEntryResult:
        assert owner_user_id == _OWNER_ID
        assert collection_id == _COLLECTION_ID
        if draft.source_candidate_id in self._fail_candidate_ids:
            raise CollectionBibliographyError(
                CollectionBibliographyErrorCode.COLLECTION_NOT_FOUND,
                "研究集合不存在、已归档或不属于当前用户。",
            )
        self.drafts.append(draft)
        status = (
            CollectionBibliographyUpsertStatus.ALREADY_PRESENT
            if draft.source_candidate_id in self._seen_candidate_ids
            else CollectionBibliographyUpsertStatus.ADDED
        )
        if draft.source_candidate_id is not None:
            self._seen_candidate_ids.add(draft.source_candidate_id)
        return CollectionBibliographyEntryResult(
            status=status,
            entry_id=uuid4(),
            collection_id=collection_id,
            content_status=draft.content_status,
        )


def _run() -> SearchRunRecord:
    """构造已结束、拥有 Redis 会话的检索运行。"""
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
        candidate_counts={"candidate_count": 2},
        error_code=None,
        error_message=None,
        started_at=now,
        finished_at=now,
        created_at=now,
        updated_at=now,
    )


def _candidate(
    candidate_id: UUID,
    *,
    title: str,
    year: int,
    doi: str | None,
) -> UnifiedCandidate:
    """构造遵循服务端搜索快照格式的候选，避免测试从前端字段拼装数据。"""
    author = CandidateAuthor(name="Ada Lovelace")
    source = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id=f"W-{candidate_id}",
        title=title,
        authors=(author,),
        doi=doi,
    )
    candidate = UnifiedCandidate(
        candidate_id=candidate_id,
        doi=doi,
        title=title,
        title_key=title.casefold(),
        authors=(author,),
        published_year=year,
        links=CandidateLinks(landing_url=f"https://doi.org/{doi}" if doi else None),
        source_records=(source,),
        triage=TriageDecision(included=True),
    )
    return _with_relevance(candidate, CandidateRelevanceLevel.CORE)


def _store(*candidates: UnifiedCandidate) -> FakeSessionStore:
    """创建包含稳定候选主快照的会话替身。"""
    return FakeSessionStore(
        {
            _SESSION_KEY: {
                "status": "completed",
                "candidate_counts": {"candidate_count": len(candidates)},
                "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            }
        }
    )


def _review_session(store: FakeSessionStore) -> CandidateReviewSession:
    """以相同运行仓储和会话替身装配审核用例。"""
    return CandidateReviewSession(
        cast(SearchRunRepository, FakeSearchRunRepository(_run())),
        cast(SearchSessionStore, store),
    )


def _with_relevance(
    candidate: UnifiedCandidate,
    level: CandidateRelevanceLevel,
) -> UnifiedCandidate:
    """为排序测试附加已验证的服务端相关性结果。"""
    return candidate.model_copy(
        update={
            "relevance_state": CandidateRelevanceState.COMPLETED,
            "relevance_assessment": CandidateRelevanceAssessment(
                level=level,
                study_focus="排序测试候选。",
                reason="用于验证相关性优先顺序。",
                helpful_aspect="用于验证相关性优先顺序。",
                recommendation="测试用。",
                evidence=(CandidateRelevanceEvidence(source_field="title", quote=candidate.title),),
            ),
            "relevance_error": None,
        }
    )


@pytest.mark.asyncio
async def test_page_keeps_selection_across_cursor_pages_and_uses_fulltext_state() -> None:
    """审核页的“正在查看”分页不影响 Redis 中跨页保存的准备选择。"""
    first = _candidate(
        _FIRST_ID,
        title="Newest verified candidate",
        year=2025,
        doi="10.1000/review.first",
    )
    second = _candidate(
        _SECOND_ID,
        title="Older candidate",
        year=2024,
        doi="10.1000/review.second",
    )
    store = _store(first, second)
    now = datetime.now(UTC)
    state_key = build_candidate_fulltext_key(_SESSION_KEY, _FIRST_ID)
    store.snapshots[state_key] = CandidateFulltextState(
        search_run_id=_RUN_ID,
        candidate=to_fulltext_candidate(first),
        attempt_no=1,
        result=FulltextAcquisitionResult(
            candidate_id=_FIRST_ID,
            status=FulltextAcquisitionStatus.QUEUED,
        ),
        requested_at=now,
        updated_at=now,
    ).model_dump(mode="json")
    session = _review_session(store)
    selection = CandidateSelectionService(session)
    query = CandidateReviewQueryService(session)

    await selection.update_selection(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_ids=[_FIRST_ID],
        selected=True,
    )
    first_page = await query.page(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        limit=1,
        cursor=None,
        query="",
        review_filter=CandidateReviewFilter.ALL,
    )
    second_page = await query.page(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        limit=1,
        cursor=first_page.page.next_cursor,
        query="",
        review_filter=CandidateReviewFilter.ALL,
    )

    assert first_page.items[0].candidate.candidate_id == _FIRST_ID
    assert first_page.items[0].is_selected is True
    assert first_page.items[0].fulltext is not None
    assert first_page.items[0].fulltext.status is FulltextAcquisitionStatus.QUEUED
    assert first_page.selection.selected_count == 1
    assert first_page.selection.fulltext_in_progress_count == 1
    assert second_page.items[0].candidate.candidate_id == _SECOND_ID
    assert second_page.items[0].is_selected is False
    assert store.refreshed_keys == [_SESSION_KEY]


@pytest.mark.asyncio
async def test_completed_review_only_returns_verified_positive_relevance_levels() -> None:
    """筛选页只展示已核验的核心、关联和背景候选。"""
    core = _with_relevance(
        _candidate(
            UUID("00000000-0000-0000-0000-000000001211"), title="Core", year=2010, doi="10.1/core"
        ),
        CandidateRelevanceLevel.CORE,
    )
    related = _with_relevance(
        _candidate(
            UUID("00000000-0000-0000-0000-000000001212"),
            title="Related",
            year=2026,
            doi="10.1/related",
        ),
        CandidateRelevanceLevel.RELATED,
    )
    background = _with_relevance(
        _candidate(
            UUID("00000000-0000-0000-0000-000000001213"),
            title="Background",
            year=2026,
            doi="10.1/background",
        ),
        CandidateRelevanceLevel.BACKGROUND,
    )
    not_recommended = _with_relevance(
        _candidate(
            UUID("00000000-0000-0000-0000-000000001214"),
            title="Not recommended",
            year=2026,
            doi="10.1/not-recommended",
        ),
        CandidateRelevanceLevel.NOT_RECOMMENDED,
    )
    insufficient = _with_relevance(
        _candidate(
            UUID("00000000-0000-0000-0000-000000001215"),
            title="Insufficient",
            year=2026,
            doi="10.1/insufficient",
        ),
        CandidateRelevanceLevel.INSUFFICIENT_INFORMATION,
    )
    pending = _candidate(
        UUID("00000000-0000-0000-0000-000000001216"),
        title="Pending",
        year=2027,
        doi="10.1/pending",
    ).model_copy(
        update={
            "relevance_state": CandidateRelevanceState.PENDING,
            "relevance_assessment": None,
        }
    )
    failed = _candidate(
        UUID("00000000-0000-0000-0000-000000001217"),
        title="Failed",
        year=2027,
        doi="10.1/failed",
    ).model_copy(
        update={
            "relevance_state": CandidateRelevanceState.FAILED,
            "relevance_error": CandidateRelevanceError(
                code="candidate_relevance_output_invalid",
                message="测试失败。",
                retryable=True,
            ),
        }
    )
    skipped = _candidate(
        UUID("00000000-0000-0000-0000-000000001218"),
        title="Skipped",
        year=2027,
        doi="10.1/skipped",
    ).model_copy(
        update={
            "triage": TriageDecision(included=False),
            "relevance_state": CandidateRelevanceState.SKIPPED,
        }
    )
    service = CandidateReviewQueryService(
        _review_session(
            _store(
                skipped, failed, pending, insufficient, not_recommended, background, related, core
            )
        )
    )

    page = await service.page(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        limit=20,
        cursor=None,
        query="",
        review_filter=CandidateReviewFilter.ALL,
    )

    assert [item.candidate.candidate_id for item in page.items] == [
        core.candidate_id,
        related.candidate_id,
        background.candidate_id,
    ]
    assert page.page.total == 3


@pytest.mark.asyncio
async def test_selection_allows_screening_candidate_without_doi() -> None:
    """缺 DOI 候选仍可被用户保存到后续研究集合。"""
    missing_doi = _candidate(
        _FIRST_ID,
        title="Candidate without DOI",
        year=2025,
        doi=None,
    )
    store = _store(missing_doi)
    session = _review_session(store)
    selection = CandidateSelectionService(session)
    query = CandidateReviewQueryService(session)

    response = await selection.update_selection(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_ids=[_FIRST_ID],
        selected=True,
    )
    page = await query.page(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        limit=20,
        cursor=None,
        query="",
        review_filter=CandidateReviewFilter.ALL,
    )

    assert response.selected_count == 1
    assert page.selection.selected_count == 1
    assert page.selection.needs_fulltext_count == 1
    assert page.selection.blocked_count == 0


@pytest.mark.asyncio
async def test_admit_selected_persists_candidate_without_citation_or_pdf_gate() -> None:
    """加入研究集合不再要求候选已有 DOI、正式题录或可自动获取 PDF。"""
    candidate = _candidate(
        _FIRST_ID,
        title="Candidate without citation or automatic PDF",
        year=2025,
        doi=None,
    )
    store = _store(candidate)
    session = _review_session(store)
    selection = CandidateSelectionService(session)
    bibliography = FakeBibliographyRepository()
    service = CandidateAdmissionService(
        session,
        cast(CollectionBibliographyRepository, bibliography),
        selection,
    )
    await selection.update_selection(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_ids=[_FIRST_ID],
        selected=True,
    )

    response = await service.admit_selected(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
    )

    draft = bibliography.drafts[0]
    assert response.selected_count == 1
    assert response.admitted_count == 1
    assert response.blocked_count == 0
    assert await session.selected_ids(_run()) == set()
    assert draft.source_search_run_id == _RUN_ID
    assert draft.source_candidate_id == _FIRST_ID
    assert draft.title == "Candidate without citation or automatic PDF"
    assert draft.doi is None
    assert draft.citation_status is BibliographyCitationStatus.UNAVAILABLE
    assert draft.citation_text is None
    assert draft.pdf_status is BibliographyPdfStatus.REQUIRES_UPLOAD
    assert draft.content_status is BibliographyContentStatus.REQUIRES_UPLOAD
    assert draft.paper_id is None


@pytest.mark.asyncio
async def test_admit_selected_preserves_ready_citation_and_available_pdf_state() -> None:
    """ready 题录才写入正式引用；公开 PDF 可得时条目进入待自动获取状态。"""
    candidate = _candidate(
        _FIRST_ID,
        title="Candidate with verified citation and PDF",
        year=2025,
        doi="10.1000/review.ready",
    ).model_copy(
        update={
            "links": CandidateLinks(
                landing_url="https://doi.org/10.1000/review.ready",
                fulltext_url="https://example.test/review-ready.pdf",
            ),
            "citation": CitationMetadata(
                status=CitationMetadataStatus.READY,
                authors=(LiteratureCitationAuthor(literal="A. Lovelace"),),
                title="Candidate with verified citation and PDF",
                document_type="journal_article",
                issued_date=CitationDate(year=2025),
                doi="10.1000/review.ready",
                url="https://doi.org/10.1000/review.ready",
            ),
            "pdf_availability": CandidatePdfAvailability(
                status=CandidatePdfAvailabilityStatus.AVAILABLE
            ),
        }
    )
    store = _store(candidate)
    session = _review_session(store)
    selection = CandidateSelectionService(session)
    bibliography = FakeBibliographyRepository()
    service = CandidateAdmissionService(
        session,
        cast(CollectionBibliographyRepository, bibliography),
        selection,
    )
    await selection.update_selection(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_ids=[_FIRST_ID],
        selected=True,
    )

    response = await service.admit_selected(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
    )

    draft = bibliography.drafts[0]
    assert response.admitted_count == 1
    assert draft.citation_status is BibliographyCitationStatus.READY
    assert draft.citation_text
    assert draft.citation_snapshot["status"] == CitationMetadataStatus.READY.value
    assert draft.pdf_status is BibliographyPdfStatus.AVAILABLE
    assert draft.pdf_source_url == "https://example.test/review-ready.pdf"
    assert draft.content_status is BibliographyContentStatus.PENDING_AUTO_DOWNLOAD


@pytest.mark.asyncio
async def test_hidden_candidate_is_not_readable_or_selectable() -> None:
    """负向、缺少初筛和旧失败候选不能经详情或选择接口重新暴露。"""
    candidate = _candidate(
        _FIRST_ID,
        title="Candidate without triage",
        year=2025,
        doi="10.1000/review.no-triage",
    ).model_copy(update={"triage": None, "relevance_assessment": None})
    session = _review_session(_store(candidate))
    selection = CandidateSelectionService(session)
    query = CandidateReviewQueryService(session)

    with pytest.raises(CandidateReviewError) as raised:
        await selection.update_selection(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            search_run_id=_RUN_ID,
            candidate_ids=[_FIRST_ID],
            selected=True,
        )

    assert raised.value.code is CandidateReviewErrorCode.CANDIDATE_NOT_FOUND

    with pytest.raises(CandidateReviewError) as item_raised:
        await query.item(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            search_run_id=_RUN_ID,
            candidate_id=_FIRST_ID,
        )

    assert item_raised.value.code is CandidateReviewErrorCode.CANDIDATE_NOT_FOUND


@pytest.mark.asyncio
async def test_page_rejects_malformed_base64_cursor_as_a_business_error() -> None:
    """非法游标必须返回可恢复的审核错误，而不能冒泡为服务端 500。"""
    candidate = _candidate(
        _FIRST_ID,
        title="Candidate with invalid cursor",
        year=2025,
        doi="10.1000/review.cursor",
    )
    service = CandidateReviewQueryService(_review_session(_store(candidate)))

    with pytest.raises(CandidateReviewError) as raised:
        await service.page(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            search_run_id=_RUN_ID,
            limit=20,
            cursor="%%%",
            query="",
            review_filter=CandidateReviewFilter.ALL,
        )

    assert raised.value.code is CandidateReviewErrorCode.INVALID_CURSOR


@pytest.mark.asyncio
async def test_item_reads_a_selected_candidate_without_scanning_a_page() -> None:
    """详情读取只依赖候选 ID，因此不受浏览器当前页或每页大小影响。"""
    candidate = _candidate(
        _FIRST_ID,
        title="Candidate detail outside active page",
        year=2025,
        doi="10.1000/review.detail",
    )
    store = _store(candidate)
    session = _review_session(store)
    selection = CandidateSelectionService(session)
    query = CandidateReviewQueryService(session)
    await selection.update_selection(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_ids=[_FIRST_ID],
        selected=True,
    )

    item = await query.item(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_id=_FIRST_ID,
    )

    assert item.candidate.candidate_id == _FIRST_ID
    assert item.is_selected is True
    assert item.fulltext is None


@pytest.mark.asyncio
async def test_prepare_selected_reuses_single_candidate_fulltext_service() -> None:
    """批量准备只负责投递既有任务，不复制题录或全文业务逻辑。"""
    candidate = _candidate(
        _FIRST_ID,
        title="Candidate to prepare",
        year=2025,
        doi="10.1000/review.prepare",
    )
    store = _store(candidate)
    queue = FakeQueue()
    runs = cast(SearchRunRepository, FakeSearchRunRepository(_run()))
    session_store = cast(SearchSessionStore, store)
    session = CandidateReviewSession(runs, session_store)
    selection = CandidateSelectionService(session)
    service = CandidatePreparationService(
        session,
        CandidateFulltextService(
            runs,
            session_store,
            queue,
            candidate_lookup=SearchCandidateFulltextLookup(runs, session_store),
        ),
    )
    await selection.update_selection(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_ids=[_FIRST_ID],
        selected=True,
    )

    response = await service.prepare_selected(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
    )

    assert response.selected_count == 1
    assert response.queued_count == 1
    assert response.items[0].status is FulltextAcquisitionStatus.QUEUED
    assert queue.calls == [(_RUN_ID, _FIRST_ID, 1)]
