"""阶段五真实端到端测试：开放获取全文进入可检索 RAG 版本。"""

from __future__ import annotations

import asyncio
import json
import os
from uuid import UUID, uuid4

import pytest
from app.db.models.collection import ResearchCollection
from app.db.models.document import Document, DocumentChunk, IngestionRun
from app.db.models.paper import Paper
from app.db.models.user import User
from app.db.session import async_session_factory
from app.modules.collections import CollectionAdmissionStatus, ResearchCollectionAdmissionService
from app.modules.collections.build_contracts import IngestionRunStatus
from app.modules.collections.build_service import ResearchCollectionBuildService
from app.modules.fulltext import (
    Boto3StagingObjectStorage,
    OpenAccessPdfAcquirer,
    get_fulltext_acquisition_settings,
)
from app.modules.fulltext.contracts import FulltextAcquisitionStatus
from app.modules.ingestion.milvus import MilvusDocumentChunkIndex
from app.modules.ingestion.settings import get_ingestion_settings
from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateLinks,
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
    RawCandidate,
    SourceName,
    UnifiedCandidate,
)
from app.modules.workflow.state import WorkspaceWorkflowStage
from app.workers.ingestion import ingest_document, startup
from pymilvus import MilvusClient
from sqlalchemy import func, select

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_INGESTION_E2E_TESTS"
_ARXIV_DOI = "10.48550/arXiv.1706.03762"
_ARXIV_PDF_URL = "https://arxiv.org/pdf/1706.03762"


class LiveQueue:
    """构建测试的队列替身；真实 Redis/arq 路由由独立测试覆盖。"""

    def __init__(self) -> None:
        self.enqueued_run_ids: list[UUID] = []

    async def enqueue_ingestion(self, ingestion_run_id: UUID) -> str:
        self.enqueued_run_ids.append(ingestion_run_id)
        return f"live-e2e-ingestion-{ingestion_run_id}"


def _live_test_is_enabled() -> bool:
    """仅在用户明确允许时访问外部服务并写入本地基础设施。"""
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


def _candidate(candidate_id: UUID) -> UnifiedCandidate:
    """构造一篇真实论文的完整、已核验候选。"""
    author = CandidateAuthor(name="Ashish Vaswani")
    citation = CitationMetadata(
        status=CitationMetadataStatus.READY,
        authors=(CitationAuthor(given="Ashish", family="Vaswani"),),
        title="Attention Is All You Need",
        document_type="article",
        issued_date=CitationDate(year=2017),
        venue="Advances in Neural Information Processing Systems",
        doi=_ARXIV_DOI,
        url=f"https://doi.org/{_ARXIV_DOI}",
        field_provenance={"doi": "arxiv"},
    )
    return UnifiedCandidate(
        candidate_id=candidate_id,
        doi=_ARXIV_DOI,
        title=citation.title,
        title_key="attention is all you need",
        authors=(author,),
        abstract="A real open-access paper used only for the local phase-five test.",
        links=CandidateLinks(fulltext_url=_ARXIV_PDF_URL),
        is_open_access=True,
        source_records=(
            RawCandidate(
                source=SourceName.ARXIV,
                source_record_id="1706.03762",
                title=citation.title,
                authors=(author,),
                doi=_ARXIV_DOI,
                fulltext_url=_ARXIV_PDF_URL,
                is_open_access=True,
            ),
        ),
        citation=citation,
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_phase_five_ingestion_reaches_researching() -> None:
    """验证全文准入、确认构建、解析分块、Qwen 向量化和 Milvus 写入闭环。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行阶段五真实测试")

    fulltext_settings = get_fulltext_acquisition_settings()
    ingestion_settings = get_ingestion_settings()
    storage = Boto3StagingObjectStorage(fulltext_settings)
    queue = LiveQueue()
    owner_user_id, collection_id, candidate_id = uuid4(), uuid4(), uuid4()
    paper_id: UUID | None = None
    document_id: UUID | None = None
    ingestion_run_id: UUID | None = None
    staging_object_key: str | None = None
    document_object_key: str | None = None
    worker_context: dict[str, object] = {}
    vector_index: MilvusDocumentChunkIndex | None = None
    candidate = _candidate(candidate_id)

    try:
        # 通过真实全文获取器访问 arXiv，先将 PDF 放入 MinIO 暂存区。
        acquired_result = await OpenAccessPdfAcquirer(fulltext_settings, storage).acquire(candidate)
        assert acquired_result.status is FulltextAcquisitionStatus.AVAILABLE
        assert acquired_result.document is not None
        staging_object_key = acquired_result.document.staging_object_key

        # 创建随机隔离的临时用户和工作区。
        async with async_session_factory() as session:
            async with session.begin():
                session.add_all(
                    (
                        User(id=owner_user_id, display_name="Phase five live test user"),
                        ResearchCollection(
                            id=collection_id,
                            owner_user_id=owner_user_id,
                            name="Phase five live ingestion test",
                            workflow_stage=WorkspaceWorkflowStage.SCREENING.value,
                        ),
                    )
                )

            # DOI、题录和全文均可用时，候选才正式进入工作区。
            admission = await ResearchCollectionAdmissionService(session, storage).admit(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                candidate=candidate,
                fulltext_result=acquired_result,
            )
            assert admission.status is CollectionAdmissionStatus.ADDED
            assert admission.paper_id and admission.document_id and admission.ingestion_run_id
            paper_id, document_id, ingestion_run_id = (
                admission.paper_id,
                admission.document_id,
                admission.ingestion_run_id,
            )
            document = await session.get(Document, document_id)
            run = await session.get(IngestionRun, ingestion_run_id)
            assert document is not None and run is not None
            assert run.status == IngestionRunStatus.PENDING.value
            document_object_key = document.object_key

        # 模拟用户确认构建：pending -> queued，并记录队列 Job ID。
        async with async_session_factory() as session:
            build_response = await ResearchCollectionBuildService(session, queue).build(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
            )
            assert build_response.runs[0].status is IngestionRunStatus.QUEUED
            assert queue.enqueued_run_ids == [ingestion_run_id]

        # 直接加载当前 Worker 代码，避免旧 Worker 抢占本次随机运行。
        await startup(worker_context)
        dependencies = worker_context["ingestion_dependencies"]
        vector_index = dependencies.vector_index  # type: ignore[union-attr]
        outcome = await ingest_document(worker_context, str(ingestion_run_id))
        assert outcome["status"] == IngestionRunStatus.COMPLETED.value
        assert int(outcome["indexed_l3_chunk_count"]) > 0

        # PostgreSQL 需同时存在 completed/current、三级分块与 researching 阶段。
        async with async_session_factory() as session:
            run = await session.get(IngestionRun, ingestion_run_id)
            collection = await session.get(ResearchCollection, collection_id)
            assert run is not None and collection is not None
            assert run.status == IngestionRunStatus.COMPLETED.value
            assert run.is_current is True
            assert run.statistics["vector_dimension"] == 1_024
            assert collection.workflow_stage == WorkspaceWorkflowStage.RESEARCHING.value

            chunk_rows = (
                await session.execute(
                    select(DocumentChunk.level, func.count(DocumentChunk.id))
                    .where(DocumentChunk.ingestion_run_id == ingestion_run_id)
                    .group_by(DocumentChunk.level)
                )
            ).all()
            level_counts = {int(level): int(count) for level, count in chunk_rows}
            assert all(level in level_counts for level in (1, 2, 3))
            assert level_counts[3] > 0

        # Milvus 只存放 L3 向量，可按本次 ingestion_run_id 精确检索。
        milvus_client = MilvusClient(
            uri=ingestion_settings.milvus_uri,
            token=(
                ingestion_settings.milvus_token.get_secret_value()
                if ingestion_settings.milvus_token
                else ""
            ),
        )
        milvus_records = await asyncio.to_thread(
            milvus_client.query,
            ingestion_settings.milvus_collection_name,
            filter=f'ingestion_run_id == "{ingestion_run_id}"',
            output_fields=["chunk_id", "level", "document_id", "collection_id"],
        )
        assert milvus_records
        assert all(record["level"] == 3 for record in milvus_records)
        assert all(record["document_id"] == str(document_id) for record in milvus_records)
        assert all(record["collection_id"] == str(collection_id) for record in milvus_records)

        print(
            json.dumps(
                {
                    "paper_title": candidate.title,
                    "source": "arxiv",
                    "doi": candidate.doi,
                    "downloaded_pdf_bytes": acquired_result.document.byte_size,
                    "document_id": str(document_id),
                    "ingestion_run_id": str(ingestion_run_id),
                    "ingestion_status": outcome["status"],
                    "vector_dimension": 1_024,
                    "postgres_chunk_counts": level_counts,
                    "milvus_l3_count": len(milvus_records),
                    "workflow_stage": WorkspaceWorkflowStage.RESEARCHING.value,
                    "cleanup": "pending",
                },
                ensure_ascii=True,
            )
        )
    finally:
        # 按运行 ID 精确删除向量，绝不删除整个 Milvus collection。
        if vector_index is not None and ingestion_run_id is not None:
            await vector_index.delete_ingestion_run(ingestion_run_id)
        # 转正后暂存对象已删除；delete_object 保持幂等，可覆盖中途失败场景。
        if document_object_key is not None:
            await storage.delete_object(object_key=document_object_key)
        if staging_object_key is not None:
            await storage.delete_object(object_key=staging_object_key)

        # 级联删除工作区记录，再独立删除本次创建的全局 Paper。
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
                    "document_object_key": document_object_key,
                    "staging_object_key": staging_object_key,
                    "ingestion_run_id": str(ingestion_run_id) if ingestion_run_id else None,
                },
                ensure_ascii=True,
            )
        )
