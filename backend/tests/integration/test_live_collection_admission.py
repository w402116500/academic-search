"""本地 PostgreSQL 与 MinIO 的研究集合文献准入集成测试。

本测试只使用随机生成的最小 PDF，不访问外部文献来源。它会在本地服务中短暂
创建用户、研究集合、书目与对象；仅在显式设置
``RUN_LIVE_COLLECTION_ADMISSION_TESTS=1`` 时运行，并会清理自身创建的数据。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from app.core.fulltext_settings import get_fulltext_acquisition_settings
from app.infra.db.models.collection import CollectionPaper, ResearchCollection
from app.infra.db.models.document import Document, IngestionRun
from app.infra.db.models.paper import Paper
from app.infra.db.models.user import User
from app.infra.db.repositories.literature_admission import (
    SqlAlchemyLiteratureAdmissionAdapter,
)
from app.infra.db.session import async_session_factory
from app.infra.storage.documents import Boto3StagingObjectStorage
from app.modules.documents.contracts import (
    AcquiredFulltext,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
)
from app.modules.literature.admission import (
    CollectionAdmissionStatus,
    LiteratureAdmissionCandidate,
)
from app.modules.literature.contracts import (
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
)
from botocore.exceptions import ClientError

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_COLLECTION_ADMISSION_TESTS"
_MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def _live_test_is_enabled() -> bool:
    """只在用户明确允许创建本地服务临时数据时执行测试。"""
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


def _candidate(candidate_id: UUID, doi: str) -> LiteratureAdmissionCandidate:
    """构造已通过 DOI 题录核验的最小准入命令。"""
    citation = CitationMetadata(
        status=CitationMetadataStatus.READY,
        authors=(CitationAuthor(given="Ada", family="Lovelace"),),
        title="Local collection admission integration test",
        document_type="journal_article",
        issued_date=CitationDate(year=2026, month=7, day=30),
        venue="Academic Search Test Journal",
        volume="1",
        pages="1-2",
        doi=doi,
        url=f"https://doi.org/{doi}",
        field_provenance={"doi": "doi_content_negotiation"},
    )

    return LiteratureAdmissionCandidate(
        candidate_id=candidate_id,
        doi=doi,
        abstract="A local integration-test record; it is not a real publication.",
        official_url=f"https://doi.org/{doi}",
        citation=citation,
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_admission_promotes_object_and_persists_research_records() -> None:
    """准入应将暂存 PDF 转正，并原子创建书目、关联、文件和入库运行。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行本地准入集成测试")

    settings = get_fulltext_acquisition_settings()
    storage = Boto3StagingObjectStorage(settings)
    owner_user_id = uuid4()
    collection_id = uuid4()
    candidate_id = uuid4()
    doi = f"10.9999/local-admission-{uuid4().hex}"
    sha256 = "a" * 64
    staging_object_key = f"{settings.fulltext_staging_prefix}/live-admission/{candidate_id}.pdf"
    document_object_key: str | None = None
    paper_id: UUID | None = None

    try:
        await storage.upload_pdf(
            object_key=staging_object_key,
            file=BytesIO(_MINIMAL_PDF),
            sha256=sha256,
        )

        async with async_session_factory() as session:
            async with session.begin():
                session.add_all(
                    (
                        User(
                            id=owner_user_id,
                            display_name="Local admission integration test user",
                        ),
                        ResearchCollection(
                            id=collection_id,
                            owner_user_id=owner_user_id,
                            name="Local admission integration test collection",
                        ),
                    )
                )

            result = await SqlAlchemyLiteratureAdmissionAdapter(session, storage).admit(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                candidate=_candidate(candidate_id, doi),
                fulltext_result=FulltextAcquisitionResult(
                    candidate_id=candidate_id,
                    status=FulltextAcquisitionStatus.AVAILABLE,
                    document=AcquiredFulltext(
                        candidate_id=candidate_id,
                        doi=doi,
                        source_url="https://downloads.example.test/local-admission.pdf",
                        staging_object_key=staging_object_key,
                        original_filename="local-admission.pdf",
                        byte_size=len(_MINIMAL_PDF),
                        sha256=sha256,
                        acquired_at=datetime.now(UTC),
                    ),
                ),
            )

            assert result.status is CollectionAdmissionStatus.ADDED
            assert result.paper_id is not None
            assert result.document_id is not None
            assert result.ingestion_run_id is not None
            paper_id = result.paper_id

            paper = await session.get(Paper, result.paper_id)
            collection_paper = await session.get(
                CollectionPaper,
                (collection_id, result.paper_id),
            )
            document = await session.get(Document, result.document_id)
            ingestion_run = await session.get(IngestionRun, result.ingestion_run_id)

            assert paper is not None and paper.doi == doi
            assert collection_paper is not None
            assert document is not None
            assert ingestion_run is not None
            assert ingestion_run.document_id == document.id
            # 新准入先停在 pending，必须由用户显式确认构建集合才会投递 Worker。
            assert ingestion_run.status == "pending"
            assert ingestion_run.stage == "parse"
            document_object_key = document.object_key

        assert document_object_key is not None
        stored_document = await asyncio.to_thread(
            storage._client.head_object,
            Bucket=settings.s3_bucket,
            Key=document_object_key,
        )
        assert stored_document["ContentLength"] == len(_MINIMAL_PDF)
        assert stored_document["Metadata"].get("sha256") == sha256

        with pytest.raises(ClientError):
            await asyncio.to_thread(
                storage._client.head_object,
                Bucket=settings.s3_bucket,
                Key=staging_object_key,
            )

        print(
            json.dumps(
                {
                    "admission_status": result.status,
                    "paper_id": str(result.paper_id),
                    "document_id": str(result.document_id),
                    "ingestion_run_id": str(result.ingestion_run_id),
                    "document_object_key": document_object_key,
                    "cleanup": "pending",
                },
                ensure_ascii=True,
            )
        )
    finally:
        if document_object_key is not None:
            await storage.delete_object(object_key=document_object_key)
        await storage.delete_object(object_key=staging_object_key)

        async with async_session_factory() as cleanup_session:
            async with cleanup_session.begin():
                user = await cleanup_session.get(User, owner_user_id)
                if user is not None:
                    await cleanup_session.delete(user)
                    await cleanup_session.flush()

                if paper_id is not None:
                    paper = await cleanup_session.get(Paper, paper_id)
                    if paper is not None:
                        await cleanup_session.delete(paper)

        print(
            json.dumps(
                {
                    "cleanup": "deleted",
                    "staging_object_key": staging_object_key,
                    "document_object_key": document_object_key,
                },
                ensure_ascii=True,
            )
        )
