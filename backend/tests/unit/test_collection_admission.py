"""研究集合文献准入服务的离线事务编排测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from app.db.models.collection import CollectionPaper, ResearchCollection
from app.db.models.document import Document, IngestionRun
from app.db.models.paper import Paper
from app.modules.collections import (
    CollectionAdmissionError,
    CollectionAdmissionErrorCode,
    CollectionAdmissionStatus,
    ResearchCollectionAdmissionService,
)
from app.modules.fulltext.contracts import (
    AcquiredFulltext,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
)
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
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000002")
_CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000003")
_PAPER_ID = UUID("00000000-0000-0000-0000-000000000004")
_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000005")
_INGESTION_RUN_ID = UUID("00000000-0000-0000-0000-000000000006")
_DOI = "10.1000/admission.example"
_SHA256 = "a" * 64
_STAGING_KEY = f"staging/fulltext/{_CANDIDATE_ID}/{_SHA256}.pdf"


class FakeSession:
    """按调用顺序返回数据库查询值的最小异步会话替身。"""

    def __init__(
        self,
        *,
        scalar_values: list[object | None],
        collection_paper: CollectionPaper | None = None,
        flush_error: Exception | None = None,
    ) -> None:
        self._scalar_values = iter(scalar_values)
        self._collection_paper = collection_paper
        self._flush_error = flush_error
        self.added: list[object] = []

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        """模拟会话事务边界；异常由服务捕获并触发对象补偿。"""
        yield self

    async def scalar(self, _statement: object) -> object | None:
        """返回预设查询值，缺少预设值时令测试立即失败。"""
        try:
            return next(self._scalar_values)
        except StopIteration as exc:
            raise AssertionError("测试缺少数据库查询预设值") from exc

    async def get(self, _entity: object, _identity: object) -> CollectionPaper | None:
        """只实现当前服务查询复合主键关联所需的 get 行为。"""
        return self._collection_paper

    def add(self, instance: object) -> None:
        """记录待插入的单个 ORM 实体。"""
        self.added.append(instance)

    def add_all(self, instances: tuple[object, ...]) -> None:
        """记录待插入的关联、文件和入库运行实体。"""
        self.added.extend(instances)

    async def flush(self) -> None:
        """按需模拟数据库约束或连接失败。"""
        if self._flush_error is not None:
            raise self._flush_error


class MemoryResearchDocumentStorage:
    """记录对象转正和删除操作，避免服务单元测试依赖 MinIO。"""

    def __init__(self) -> None:
        self.promotions: list[tuple[str, str, str]] = []
        self.deleted: list[str] = []

    async def promote_staged_pdf(
        self,
        *,
        staging_object_key: str,
        document_object_key: str,
        sha256: str,
    ) -> None:
        """记录服务端复制与暂存清理已成功完成的转正操作。"""
        self.promotions.append((staging_object_key, document_object_key, sha256))

    async def delete_object(self, *, object_key: str) -> None:
        """记录精确删除，不模拟 bucket 级别清理。"""
        self.deleted.append(object_key)


def _collection() -> ResearchCollection:
    """构造属于当前测试用户的活动研究集合。"""
    return ResearchCollection(
        id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        name="Admission test collection",
        status="active",
    )


def _candidate() -> UnifiedCandidate:
    """构造具有完整 DOI 题录的统一候选。"""
    author = CandidateAuthor(name="Ada Lovelace")
    citation = CitationMetadata(
        status=CitationMetadataStatus.READY,
        authors=(CitationAuthor(given="Ada", family="Lovelace"),),
        title="A verified paper for collection admission",
        document_type="journal_article",
        issued_date=CitationDate(year=2024, month=5, day=1),
        venue="Journal of Admission Tests",
        volume="12",
        pages="1-10",
        doi=_DOI,
        url=f"https://doi.org/{_DOI}",
        field_provenance={"doi": "doi_content_negotiation"},
    )
    raw = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id="W-admission-test",
        title=citation.title,
        authors=(author,),
        doi=_DOI,
    )

    return UnifiedCandidate(
        candidate_id=_CANDIDATE_ID,
        doi=_DOI,
        title=citation.title,
        title_key="a verified paper for collection admission",
        authors=(author,),
        abstract="A concise abstract used to test persistence.",
        links=CandidateLinks(landing_url=f"https://doi.org/{_DOI}"),
        is_open_access=True,
        source_records=(raw,),
        citation=citation,
    )


def _available_fulltext(*, candidate_id: UUID = _CANDIDATE_ID) -> FulltextAcquisitionResult:
    """构造已进入暂存区的合法 PDF 获取结果。"""
    document = AcquiredFulltext(
        candidate_id=candidate_id,
        doi=_DOI,
        source_url="https://downloads.example.test/admission.pdf",
        staging_object_key=_STAGING_KEY,
        original_filename="admission.pdf",
        byte_size=1_024,
        sha256=_SHA256,
        acquired_at=datetime.now(UTC),
    )
    return FulltextAcquisitionResult(
        candidate_id=candidate_id,
        status=FulltextAcquisitionStatus.AVAILABLE,
        document=document,
    )


@pytest.mark.asyncio
async def test_admit_creates_paper_collection_document_and_pending_ingestion_run() -> None:
    """新的合格候选应只在对象转正后被一次事务写为完整研究文献。"""
    session = FakeSession(
        scalar_values=[_collection(), None, _collection(), None, None],
    )
    storage = MemoryResearchDocumentStorage()
    service = ResearchCollectionAdmissionService(cast(AsyncSession, session), storage)

    result = await service.admit(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        candidate=_candidate(),
        fulltext_result=_available_fulltext(),
    )

    paper = next(item for item in session.added if isinstance(item, Paper))
    collection_paper = next(item for item in session.added if isinstance(item, CollectionPaper))
    document = next(item for item in session.added if isinstance(item, Document))
    ingestion_run = next(item for item in session.added if isinstance(item, IngestionRun))

    assert result.status is CollectionAdmissionStatus.ADDED
    assert result.paper_id == paper.id
    assert result.document_id == document.id
    assert result.ingestion_run_id == ingestion_run.id
    assert paper.doi == _DOI
    assert paper.authors == [{"family": "Lovelace", "given": "Ada"}]
    assert collection_paper.collection_id == _COLLECTION_ID
    assert collection_paper.paper_id == paper.id
    assert document.object_key.startswith(f"documents/{_COLLECTION_ID}/{document.id}/")
    assert document.object_key.endswith(f"{_SHA256}.pdf")
    assert ingestion_run.document_id == document.id
    assert ingestion_run.status == "pending"
    assert ingestion_run.stage == "parse"
    assert storage.promotions == [(_STAGING_KEY, document.object_key, _SHA256)]
    assert not storage.deleted


@pytest.mark.asyncio
async def test_admit_is_idempotent_and_discards_the_new_staging_object() -> None:
    """同一论文再次加入同一集合时不能产生第二条文件或入库运行。"""
    paper = Paper(
        id=_PAPER_ID,
        doi=_DOI,
        title="Existing paper",
        authors=[{"literal": "Ada Lovelace"}],
        citation_text="[1] Existing paper.",
        citation_provider="doi_content_negotiation",
    )
    collection_paper = CollectionPaper(collection_id=_COLLECTION_ID, paper_id=_PAPER_ID)
    document = Document(
        id=_DOCUMENT_ID,
        collection_id=_COLLECTION_ID,
        paper_id=_PAPER_ID,
        origin_kind="open_access",
        original_filename="existing.pdf",
        media_type="application/pdf",
        byte_size=1_024,
        sha256="b" * 64,
        object_key="documents/existing.pdf",
        source_url="https://downloads.example.test/existing.pdf",
        access_rights="open_access",
    )
    session = FakeSession(
        scalar_values=[_collection(), paper, document, _INGESTION_RUN_ID],
        collection_paper=collection_paper,
    )
    storage = MemoryResearchDocumentStorage()
    service = ResearchCollectionAdmissionService(cast(AsyncSession, session), storage)

    result = await service.admit(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        candidate=_candidate(),
        fulltext_result=_available_fulltext(),
    )

    assert result.status is CollectionAdmissionStatus.ALREADY_JOINED
    assert result.paper_id == _PAPER_ID
    assert result.document_id == _DOCUMENT_ID
    assert result.ingestion_run_id == _INGESTION_RUN_ID
    assert not session.added
    assert not storage.promotions
    assert storage.deleted == [_STAGING_KEY]


@pytest.mark.asyncio
async def test_admit_cleans_both_object_keys_when_database_persistence_fails() -> None:
    """对象转正后数据库失败时，正式对象和暂存对象都必须进入补偿清理。"""
    session = FakeSession(
        scalar_values=[_collection(), None, _collection(), None, None],
        flush_error=SQLAlchemyError("forced database failure"),
    )
    storage = MemoryResearchDocumentStorage()
    service = ResearchCollectionAdmissionService(cast(AsyncSession, session), storage)

    with pytest.raises(CollectionAdmissionError) as raised:
        await service.admit(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            candidate=_candidate(),
            fulltext_result=_available_fulltext(),
        )

    assert raised.value.code is CollectionAdmissionErrorCode.PERSISTENCE_ERROR
    assert len(storage.promotions) == 1
    promoted_key = storage.promotions[0][1]
    assert storage.deleted == [promoted_key, _STAGING_KEY]


@pytest.mark.asyncio
async def test_admit_rejects_fulltext_from_a_different_candidate_without_storage_actions() -> None:
    """候选与全文结果的临时 ID 不同，不能通过后续数据库操作掩盖边界错误。"""
    session = FakeSession(scalar_values=[])
    storage = MemoryResearchDocumentStorage()
    service = ResearchCollectionAdmissionService(cast(AsyncSession, session), storage)

    with pytest.raises(CollectionAdmissionError) as raised:
        await service.admit(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            candidate=_candidate(),
            fulltext_result=_available_fulltext(candidate_id=uuid4()),
        )

    assert raised.value.code is CollectionAdmissionErrorCode.FULLTEXT_MISMATCH
    assert not session.added
    assert not storage.promotions
    assert not storage.deleted
