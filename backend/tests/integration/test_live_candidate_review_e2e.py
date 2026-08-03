"""候选准备清单到真实开放全文准入的端到端验收。

测试将一个 arXiv 开放 PDF 依次经过 Redis 准备清单、全文 Worker、MinIO 暂存与
批量准入服务。只在显式设置 ``RUN_LIVE_CANDIDATE_REVIEW_E2E_TESTS=1`` 时执行，
并精确删除本轮创建的 Redis、对象与数据库记录。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.db.models.collection import ResearchCollection
from app.db.models.document import Document, IngestionRun
from app.db.models.paper import Paper
from app.db.models.user import User
from app.db.models.workflow import ResearchPlan, SearchRun
from app.db.session import async_session_factory
from app.modules.fulltext import Boto3StagingObjectStorage, get_fulltext_acquisition_settings
from app.modules.fulltext.contracts import FulltextAcquisitionStatus
from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateLinks,
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.workflow.candidate_review_service import CandidateReviewService
from app.modules.workflow.search_session import (
    SearchSessionStore,
    build_candidate_fulltext_key,
    build_candidate_selection_key,
    build_search_session_key,
)
from app.modules.workflow.state import ResearchPlanStatus, SearchRunStage, SearchRunStatus
from app.workers.fulltext import acquire_candidate_fulltext
from app.workers.redis import redis_client_from_environment
from sqlalchemy import select

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_CANDIDATE_REVIEW_E2E_TESTS"
_ARXIV_DOI = "10.48550/arXiv.1706.03762"
_ARXIV_PDF_URL = "https://arxiv.org/pdf/1706.03762"


class FakeFulltextQueue:
    """仅替代 arq 投递，让测试在当前进程中显式运行真实 Worker。"""

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
        return f"live-candidate-review-{candidate_id}-{attempt_no}"


def _live_test_is_enabled() -> bool:
    """避免普通测试无意下载外部 PDF 或写入本地基础设施。"""
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


def _candidate(candidate_id: UUID) -> UnifiedCandidate:
    """构造 DOI、题录和开放全文链接都可验证的真实候选。"""
    author = CandidateAuthor(name="Ashish Vaswani")
    return UnifiedCandidate(
        candidate_id=candidate_id,
        doi=_ARXIV_DOI,
        title="Attention Is All You Need",
        title_key="attention is all you need",
        authors=(author,),
        links=CandidateLinks(fulltext_url=_ARXIV_PDF_URL),
        is_open_access=True,
        source_records=(
            RawCandidate(
                source=SourceName.ARXIV,
                source_record_id="1706.03762",
                title="Attention Is All You Need",
                authors=(author,),
                doi=_ARXIV_DOI,
                fulltext_url=_ARXIV_PDF_URL,
                is_open_access=True,
            ),
        ),
        citation=CitationMetadata(
            status=CitationMetadataStatus.READY,
            authors=(CitationAuthor(given="Ashish", family="Vaswani"),),
            title="Attention Is All You Need",
            document_type="article",
            issued_date=CitationDate(year=2017),
            venue="Advances in Neural Information Processing Systems",
            doi=_ARXIV_DOI,
            url=f"https://doi.org/{_ARXIV_DOI}",
        ),
        triage=TriageDecision(included=True),
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_candidate_review_prepares_downloads_and_admits_an_open_pdf() -> None:
    """批量准备必须经真实 Worker 获得正文，随后才能进入待确认集合。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实候选审核验收")

    owner_user_id = uuid4()
    collection_id = uuid4()
    plan_id = uuid4()
    run_id = uuid4()
    candidate_id = uuid4()
    session_key = build_search_session_key(run_id)
    fulltext_key = build_candidate_fulltext_key(session_key, candidate_id)
    selection_key = build_candidate_selection_key(session_key)
    redis = redis_client_from_environment()
    storage = Boto3StagingObjectStorage(get_fulltext_acquisition_settings())
    document_object_key: str | None = None
    paper_id: UUID | None = None
    paper_preexisted = False

    try:
        async with async_session_factory() as session:
            paper_preexisted = (
                await session.scalar(select(Paper.id).where(Paper.doi == _ARXIV_DOI))
            ) is not None
            session.add_all(
                (
                    User(
                        id=owner_user_id,
                        email=f"live-candidate-review-{owner_user_id}@example.invalid",
                        display_name="Live candidate review user",
                        status="active",
                    ),
                    ResearchCollection(
                        id=collection_id,
                        owner_user_id=owner_user_id,
                        name="Live candidate review collection",
                        research_question="How does attention improve sequence modelling?",
                        status="active",
                        workflow_stage="screening",
                    ),
                    ResearchPlan(
                        id=plan_id,
                        collection_id=collection_id,
                        revision=1,
                        raw_request="How does attention improve sequence modelling?",
                        status=ResearchPlanStatus.CONFIRMED.value,
                        direction_options=[],
                        selected_direction_id="attention",
                        scope={"confirmed": {"languages": ["en"]}},
                        query_plan={"selected_direction_id": "attention", "queries": []},
                        model_snapshot={"provider": "live-candidate-review"},
                        confirmed_at=datetime.now(UTC),
                    ),
                    SearchRun(
                        id=run_id,
                        collection_id=collection_id,
                        research_plan_id=plan_id,
                        redis_session_key=session_key,
                        status=SearchRunStatus.COMPLETED.value,
                        stage=SearchRunStage.COMPLETED.value,
                        attempt_no=1,
                        provider_summary={"arxiv": {"status": "completed", "candidate_count": 1}},
                        candidate_counts={"candidate_count": 1},
                        finished_at=datetime.now(UTC),
                    ),
                )
            )
            await session.commit()

        store = SearchSessionStore(redis, ttl_seconds=600)
        candidate = _candidate(candidate_id)
        await store.write_snapshot(
            session_key,
            {
                "run_id": str(run_id),
                "status": SearchRunStatus.COMPLETED.value,
                "candidate_counts": {"candidate_count": 1},
                "candidates": [candidate.model_dump(mode="json")],
            },
        )

        queue = FakeFulltextQueue()
        async with async_session_factory() as session:
            review = CandidateReviewService(
                session,
                store,
                fulltext_queue=queue,
            )
            await review.update_selection(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                search_run_id=run_id,
                candidate_ids=[candidate_id],
                selected=True,
            )
            prepared = await review.prepare_selected(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                search_run_id=run_id,
            )

        assert prepared.queued_count == 1
        assert queue.calls == [(run_id, candidate_id, 1)]

        # 与生产 Worker 完全相同的函数实际访问 arXiv、验证 PDF 并写入本地 MinIO。
        await acquire_candidate_fulltext(
            {},
            search_run_id=str(run_id),
            candidate_id=str(candidate_id),
            attempt_no=1,
        )
        fulltext_state = await store.read_snapshot(fulltext_key)
        assert fulltext_state is not None
        assert fulltext_state["result"]["status"] == FulltextAcquisitionStatus.AVAILABLE.value

        async with async_session_factory() as session:
            review = CandidateReviewService(
                session,
                store,
                admission_storage=storage,
            )
            admitted = await review.admit_selected(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                search_run_id=run_id,
            )

            assert admitted.admitted_count == 1
            assert admitted.blocked_count == 0
            document = await session.scalar(
                select(Document).where(Document.collection_id == collection_id)
            )
            assert document is not None
            # 文档与入库运行是一对多关系；准入完成后应创建一条待投递的运行记录。
            ingestion_run = await session.scalar(
                select(IngestionRun).where(IngestionRun.document_id == document.id)
            )
            assert ingestion_run is not None
            assert ingestion_run.status == "pending"
            document_object_key = document.object_key
            paper_id = document.paper_id

        assert await store.read_snapshot(selection_key) == {"candidate_ids": []}
    finally:
        if document_object_key is not None:
            await storage.delete_object(object_key=document_object_key)
        await redis.delete(session_key, fulltext_key, selection_key)
        async with async_session_factory() as cleanup_session:
            user = await cleanup_session.get(User, owner_user_id)
            if user is not None:
                await cleanup_session.delete(user)
                await cleanup_session.flush()
            if paper_id is not None and not paper_preexisted:
                paper = await cleanup_session.get(Paper, paper_id)
                if paper is not None:
                    await cleanup_session.delete(paper)
            await cleanup_session.commit()
        await redis.aclose()
