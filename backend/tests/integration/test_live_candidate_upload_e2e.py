"""有权处理 PDF 上传到候选准入的真实基础设施验收。

测试只在显式设置 ``RUN_LIVE_CANDIDATE_UPLOAD_E2E_TESTS=1`` 时运行。它将随机候选
的授权 PDF 写入 MinIO 暂存区，再通过既有候选审核服务完成严格准入，并清理所有临时
Redis、对象存储和 PostgreSQL 资源。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.core.fulltext_settings import get_fulltext_acquisition_settings
from app.infra.db.models.collection import ResearchCollection
from app.infra.db.models.document import Document, IngestionRun
from app.infra.db.models.paper import Paper
from app.infra.db.models.user import User
from app.infra.db.models.workflow import ResearchPlan, SearchRun
from app.infra.db.repositories.literature_admission import (
    SqlAlchemyLiteratureAdmissionAdapter,
)
from app.infra.db.repositories.search_runs import SqlAlchemySearchRunRepository
from app.infra.db.session import async_session_factory
from app.infra.redis.connection import redis_client_from_environment
from app.infra.redis.search_session import RedisSearchSessionStore
from app.infra.storage.documents import Boto3StagingObjectStorage
from app.modules.documents.acquisition import AuthorizedPdfUploader
from app.modules.documents.contracts import FulltextAcquisitionStatus
from app.modules.documents.keys import build_candidate_fulltext_key
from app.modules.documents.service import CandidateFulltextService
from app.modules.literature.contracts import (
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
)
from app.modules.research.state import ResearchPlanStatus
from app.modules.search.contracts import (
    CandidateAuthor,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.search.fulltext_candidate import SearchCandidateFulltextLookup
from app.modules.search.review_admission import CandidateAdmissionService
from app.modules.search.review_selection import CandidateSelectionService
from app.modules.search.review_session import CandidateReviewSession
from app.modules.search.session import (
    build_candidate_selection_key,
    build_search_session_key,
)
from app.modules.search.state import SearchRunStage, SearchRunStatus
from sqlalchemy import select

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_CANDIDATE_UPLOAD_E2E_TESTS"
_PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def _live_test_is_enabled() -> bool:
    """避免日常测试无意写入本地对象存储和数据库。"""
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


def _candidate(candidate_id: UUID, doi: str) -> UnifiedCandidate:
    """只将 DOI、题录与候选身份写入服务端 Redis 搜索会话。"""
    author = CandidateAuthor(name="Upload Verification Author")
    return UnifiedCandidate(
        candidate_id=candidate_id,
        doi=doi,
        title="Authorized PDF upload verification",
        title_key="authorized pdf upload verification",
        authors=(author,),
        source_records=(
            RawCandidate(
                source=SourceName.OPENALEX,
                source_record_id=f"live-upload-{candidate_id}",
                title="Authorized PDF upload verification",
                authors=(author,),
                doi=doi,
            ),
        ),
        citation=CitationMetadata(
            status=CitationMetadataStatus.READY,
            authors=(CitationAuthor(given="Upload", family="Verification"),),
            title="Authorized PDF upload verification",
            document_type="article",
            issued_date=CitationDate(year=2026),
            venue="Local upload acceptance fixture",
            doi=doi,
            url=f"https://doi.org/{doi}",
        ),
        triage=TriageDecision(included=True),
    )


async def _pdf_chunks() -> AsyncIterator[bytes]:
    """模拟二进制请求流，而非让测试直接传对象键或本地文件路径。"""
    yield _PDF_BYTES[:17]
    yield _PDF_BYTES[17:]


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_authorized_pdf_upload_is_staged_and_admitted_for_its_candidate() -> None:
    """授权上传只能使用服务端候选身份，且必须复用严格准入链路。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实上传验收")

    owner_user_id = uuid4()
    collection_id = uuid4()
    plan_id = uuid4()
    run_id = uuid4()
    candidate_id = uuid4()
    doi = f"10.5555/live-upload-{uuid4().hex}"
    session_key = build_search_session_key(run_id)
    fulltext_key = build_candidate_fulltext_key(session_key, candidate_id)
    selection_key = build_candidate_selection_key(session_key)
    redis = redis_client_from_environment()
    acquisition_settings = get_fulltext_acquisition_settings()
    storage = Boto3StagingObjectStorage(acquisition_settings)
    document_object_key: str | None = None
    staging_object_key: str | None = None
    paper_id: UUID | None = None

    try:
        async with async_session_factory() as session:
            session.add_all(
                (
                    User(
                        id=owner_user_id,
                        email=f"live-candidate-upload-{owner_user_id}@example.invalid",
                        display_name="Live candidate upload user",
                        status="active",
                    ),
                    ResearchCollection(
                        id=collection_id,
                        owner_user_id=owner_user_id,
                        name="Live candidate upload collection",
                        research_question="Can an authorized PDF enter the review collection?",
                        status="active",
                        workflow_stage="screening",
                    ),
                    ResearchPlan(
                        id=plan_id,
                        collection_id=collection_id,
                        revision=1,
                        raw_request="Can an authorized PDF enter the review collection?",
                        status=ResearchPlanStatus.CONFIRMED.value,
                        direction_options=[],
                        selected_direction_id="authorized-upload",
                        scope={"confirmed": {"languages": ["en"]}},
                        query_plan={"selected_direction_id": "authorized-upload", "queries": []},
                        model_snapshot={"provider": "live-candidate-upload"},
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
                        provider_summary={"upload": {"status": "completed", "candidate_count": 1}},
                        candidate_counts={"candidate_count": 1},
                        finished_at=datetime.now(UTC),
                    ),
                )
            )
            await session.commit()

        store = RedisSearchSessionStore(redis, ttl_seconds=600)
        candidate = _candidate(candidate_id, doi)
        await store.write_snapshot(
            session_key,
            {
                "run_id": str(run_id),
                "status": SearchRunStatus.COMPLETED.value,
                "candidate_counts": {"candidate_count": 1},
                "candidates": [candidate.model_dump(mode="json")],
            },
        )

        async with async_session_factory() as session:
            runs = SqlAlchemySearchRunRepository(session)
            submission = await CandidateFulltextService(
                runs,
                store,
                candidate_lookup=SearchCandidateFulltextLookup(runs, store),
                uploader=AuthorizedPdfUploader(acquisition_settings, storage),
            ).upload(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                search_run_id=run_id,
                candidate_id=candidate_id,
                authorized_to_process=True,
                chunks=_pdf_chunks(),
                media_type="application/pdf",
            )

        assert submission.state.result.status is FulltextAcquisitionStatus.AVAILABLE
        uploaded = submission.state.result.document
        assert uploaded is not None
        assert uploaded.doi == doi
        assert uploaded.origin_kind == "user_upload"
        assert uploaded.access_rights == "user_upload"
        assert uploaded.source_url == f"user-upload://candidate/{candidate_id}"
        assert uploaded.byte_size == len(_PDF_BYTES)
        assert uploaded.sha256 == hashlib.sha256(_PDF_BYTES).hexdigest()
        staging_object_key = uploaded.staging_object_key

        async with async_session_factory() as session:
            runs = SqlAlchemySearchRunRepository(session)
            review_session = CandidateReviewSession(runs, store)
            selection = CandidateSelectionService(review_session)
            await selection.update_selection(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                search_run_id=run_id,
                candidate_ids=[candidate_id],
                selected=True,
            )
            admitted = await CandidateAdmissionService(
                review_session,
                CandidateFulltextService(
                    runs,
                    store,
                    candidate_lookup=SearchCandidateFulltextLookup(runs, store),
                ),
                SqlAlchemyLiteratureAdmissionAdapter(session, storage),
                selection,
            ).admit_selected(
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
            assert document.origin_kind == "user_upload"
            assert document.access_rights == "user_upload"
            assert document.sha256 == hashlib.sha256(_PDF_BYTES).hexdigest()
            ingestion_run = await session.scalar(
                select(IngestionRun).where(IngestionRun.document_id == document.id)
            )
            assert ingestion_run is not None
            assert ingestion_run.status == "pending"
            document_object_key = document.object_key
            paper_id = document.paper_id

        assert await store.read_snapshot(selection_key) == {"candidate_ids": []}
        print("live authorized candidate upload acceptance passed")
    finally:
        if document_object_key is not None:
            await storage.delete_object(object_key=document_object_key)
        if staging_object_key is not None:
            await storage.delete_object(object_key=staging_object_key)
        await redis.delete(session_key, fulltext_key, selection_key)
        async with async_session_factory() as cleanup_session:
            user = await cleanup_session.get(User, owner_user_id)
            if user is not None:
                await cleanup_session.delete(user)
                await cleanup_session.flush()
            if paper_id is not None:
                paper = await cleanup_session.get(Paper, paper_id)
                if paper is not None:
                    await cleanup_session.delete(paper)
            await cleanup_session.commit()
        await redis.aclose()
