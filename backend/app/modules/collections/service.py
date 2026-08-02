"""将已验证候选与暂存 PDF 原子纳入研究集合的业务服务。"""

from __future__ import annotations

from typing import Final
from uuid import UUID, uuid4

from app.db.models.collection import CollectionPaper, ResearchCollection
from app.db.models.document import Document, IngestionRun
from app.db.models.paper import Paper
from app.modules.collections.contracts import (
    CollectionAdmissionError,
    CollectionAdmissionErrorCode,
    CollectionAdmissionResult,
    CollectionAdmissionStatus,
)
from app.modules.fulltext.contracts import (
    AcquiredFulltext,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
)
from app.modules.fulltext.storage import FulltextStorageError, ResearchDocumentObjectStorage
from app.modules.search.citation_formatter import (
    CitationFormat,
    CitationFormattingError,
    format_citation,
)
from app.modules.search.contracts import CitationMetadata, CitationMetadataStatus, UnifiedCandidate
from app.modules.search.normalize import normalize_document_type, normalize_doi
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

_PIPELINE_VERSION: Final = "rag-ingestion-v1"
_DOCUMENT_ORIGIN_KINDS: Final = frozenset({"open_access", "official_download", "user_upload"})
_DOCUMENT_ACCESS_RIGHTS: Final = frozenset({"open_access", "official_allowed", "user_upload"})


class ResearchCollectionAdmissionService:
    """在对象存储与 PostgreSQL 之间编排可补偿的文献准入。"""

    def __init__(
        self,
        session: AsyncSession,
        storage: ResearchDocumentObjectStorage,
    ) -> None:
        """注入请求范围内会话和对象存储，避免服务自行创建全局连接。"""
        self._session = session
        self._storage = storage

    async def admit(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        candidate: UnifiedCandidate,
        fulltext_result: FulltextAcquisitionResult,
    ) -> CollectionAdmissionResult:
        """把一个已核验候选和其暂存 PDF 加入用户拥有的活动研究集合。"""
        citation, acquired = self._validate_admission_input(candidate, fulltext_result)
        doi = self._required_doi(candidate, citation, acquired)

        # 先识别正常的重复加入，避免为幂等请求执行无意义的对象复制。
        existing = await self._find_existing_admission(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            doi=doi,
        )
        if existing is not None:
            await self._discard_staging_object(acquired.staging_object_key)
            return existing

        document_id = uuid4()
        document_object_key = self._document_object_key(
            collection_id=collection_id,
            document_id=document_id,
            sha256=acquired.sha256,
        )

        try:
            await self._storage.promote_staged_pdf(
                staging_object_key=acquired.staging_object_key,
                document_object_key=document_object_key,
                sha256=acquired.sha256,
            )
            result = await self._persist_admission(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                candidate=candidate,
                citation=citation,
                acquired=acquired,
                doi=doi,
                document_id=document_id,
                document_object_key=document_object_key,
            )
        except FulltextStorageError as exc:
            await self._cleanup_failed_admission(
                staging_object_key=acquired.staging_object_key,
                document_object_key=document_object_key,
                original_error=exc,
            )
            raise CollectionAdmissionError(
                CollectionAdmissionErrorCode.STORAGE_ERROR,
                "文献文件无法安全转入研究集合，请稍后重试。",
                retryable=True,
            ) from exc
        except SQLAlchemyError as exc:
            await self._cleanup_failed_admission(
                staging_object_key=acquired.staging_object_key,
                document_object_key=document_object_key,
                original_error=exc,
            )
            raise CollectionAdmissionError(
                CollectionAdmissionErrorCode.PERSISTENCE_ERROR,
                "文献元数据暂时无法保存，请稍后重试。",
                retryable=True,
            ) from exc
        except CollectionAdmissionError as exc:
            await self._cleanup_failed_admission(
                staging_object_key=acquired.staging_object_key,
                document_object_key=document_object_key,
                original_error=exc,
            )
            raise

        if result.status is CollectionAdmissionStatus.ALREADY_JOINED:
            await self._delete_promoted_object(document_object_key)

        return result

    async def _find_existing_admission(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        doi: str,
    ) -> CollectionAdmissionResult | None:
        """在复制对象前检查活动工作区和同一 DOI 的既有集合关联。"""
        async with self._session.begin():
            await self._require_active_collection(owner_user_id, collection_id)
            paper = await self._paper_by_doi(doi)

            if paper is None:
                return None

            return await self._existing_admission_result(
                collection_id=collection_id, paper_id=paper.id
            )

    async def _persist_admission(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        candidate: UnifiedCandidate,
        citation: CitationMetadata,
        acquired: AcquiredFulltext,
        doi: str,
        document_id: UUID,
        document_object_key: str,
    ) -> CollectionAdmissionResult:
        """在单个事务中写入论文、工作区关联、文件和初始入库运行。"""
        async with self._session.begin():
            await self._require_active_collection(owner_user_id, collection_id)
            paper = await self._paper_by_doi(doi)

            if paper is not None:
                existing = await self._existing_admission_result(
                    collection_id=collection_id,
                    paper_id=paper.id,
                )
                if existing is not None:
                    return existing
            else:
                paper = self._build_paper(candidate=candidate, citation=citation, doi=doi)
                self._session.add(paper)

            existing_content = await self._session.scalar(
                select(Document).where(
                    Document.collection_id == collection_id,
                    Document.sha256 == acquired.sha256,
                )
            )
            if existing_content is not None:
                raise CollectionAdmissionError(
                    CollectionAdmissionErrorCode.DUPLICATE_DOCUMENT,
                    "该 PDF 已作为另一篇文献加入当前研究集合，不能重复使用。",
                )

            collection_paper = CollectionPaper(collection_id=collection_id, paper_id=paper.id)
            document = Document(
                id=document_id,
                collection_id=collection_id,
                paper_id=paper.id,
                origin_kind=acquired.origin_kind,
                original_filename=acquired.original_filename,
                media_type=acquired.media_type,
                byte_size=acquired.byte_size,
                sha256=acquired.sha256,
                object_key=document_object_key,
                source_url=acquired.source_url,
                access_rights=acquired.access_rights,
            )
            ingestion_run = IngestionRun(
                document_id=document_id,
                pipeline_version=_PIPELINE_VERSION,
                # 用户尚未确认构建集合；此时文件可审核，但绝不能被 Worker 领取。
                status="pending",
                stage="parse",
                chunking_config={},
                embedding_config={},
                statistics={},
                attempt_no=1,
                is_current=False,
            )
            self._session.add_all((collection_paper, document, ingestion_run))
            await self._session.flush()

            return CollectionAdmissionResult(
                status=CollectionAdmissionStatus.ADDED,
                collection_id=collection_id,
                paper_id=paper.id,
                document_id=document_id,
                ingestion_run_id=ingestion_run.id,
            )

    async def _require_active_collection(
        self,
        owner_user_id: UUID,
        collection_id: UUID,
    ) -> ResearchCollection:
        """锁定当前用户的活动工作区，防止归档或越权集合进入 RAG 范围。"""
        collection = await self._session.scalar(
            select(ResearchCollection)
            .where(
                ResearchCollection.id == collection_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status == "active",
            )
            .with_for_update()
        )
        if collection is None:
            raise CollectionAdmissionError(
                CollectionAdmissionErrorCode.COLLECTION_UNAVAILABLE,
                "研究集合不存在、不可用或不属于当前用户。",
            )

        return collection

    async def _paper_by_doi(self, doi: str) -> Paper | None:
        """按唯一 DOI 获取长期论文；已有论文在新工作区中可复用规范题录。"""
        return await self._session.scalar(select(Paper).where(Paper.doi == doi).with_for_update())

    async def _existing_admission_result(
        self,
        *,
        collection_id: UUID,
        paper_id: UUID,
    ) -> CollectionAdmissionResult | None:
        """查询当前工作区是否已包含此论文，并返回已有文件与入库运行标识。"""
        collection_paper = await self._session.get(
            CollectionPaper,
            (collection_id, paper_id),
        )
        if collection_paper is None:
            return None

        document = await self._session.scalar(
            select(Document)
            .where(Document.collection_id == collection_id, Document.paper_id == paper_id)
            .order_by(Document.created_at.asc())
            .limit(1)
        )
        ingestion_run_id: UUID | None = None

        if document is not None:
            ingestion_run_id = await self._session.scalar(
                select(IngestionRun.id)
                .where(IngestionRun.document_id == document.id)
                .order_by(IngestionRun.created_at.desc())
                .limit(1)
            )

        return CollectionAdmissionResult(
            status=CollectionAdmissionStatus.ALREADY_JOINED,
            collection_id=collection_id,
            paper_id=paper_id,
            document_id=document.id if document is not None else None,
            ingestion_run_id=ingestion_run_id,
        )

    def _validate_admission_input(
        self,
        candidate: UnifiedCandidate,
        fulltext_result: FulltextAcquisitionResult,
    ) -> tuple[CitationMetadata, AcquiredFulltext]:
        """确认题录、候选和暂存文件属于同一篇可长期研究的论文。"""
        citation = candidate.citation

        if citation is None or citation.status is not CitationMetadataStatus.READY:
            raise CollectionAdmissionError(
                CollectionAdmissionErrorCode.CITATION_NOT_READY,
                "题录尚未完成 DOI 核验，不能加入研究集合。",
            )

        if (
            fulltext_result.status is not FulltextAcquisitionStatus.AVAILABLE
            or fulltext_result.document is None
        ):
            raise CollectionAdmissionError(
                CollectionAdmissionErrorCode.FULLTEXT_UNAVAILABLE,
                "尚未取得并校验可处理的全文，不能加入研究集合。",
                retryable=True,
            )

        acquired = fulltext_result.document
        candidate_doi = normalize_doi(candidate.doi)
        citation_doi = normalize_doi(citation.doi)
        acquired_doi = normalize_doi(acquired.doi)

        if (
            fulltext_result.candidate_id != candidate.candidate_id
            or acquired.candidate_id != candidate.candidate_id
            or candidate_doi is None
            or candidate_doi != citation_doi
            or candidate_doi != acquired_doi
            or acquired.origin_kind not in _DOCUMENT_ORIGIN_KINDS
            or acquired.access_rights not in _DOCUMENT_ACCESS_RIGHTS
            or acquired.media_type != "application/pdf"
        ):
            raise CollectionAdmissionError(
                CollectionAdmissionErrorCode.FULLTEXT_MISMATCH,
                "全文结果与已核验候选不一致，不能加入研究集合。",
            )

        try:
            format_citation(citation, CitationFormat.GB_T_7714_2015_NUMERIC)
        except CitationFormattingError as exc:
            raise CollectionAdmissionError(
                CollectionAdmissionErrorCode.CITATION_NOT_READY,
                "已核验题录无法生成默认 GB/T 引用，不能加入研究集合。",
            ) from exc

        return citation, acquired

    @staticmethod
    def _required_doi(
        candidate: UnifiedCandidate,
        citation: CitationMetadata,
        acquired: AcquiredFulltext,
    ) -> str:
        """在跨阶段 DOI 已比较一致后，返回唯一的规范数据库键。"""
        doi = normalize_doi(candidate.doi)

        if doi is None or doi != normalize_doi(citation.doi) or doi != normalize_doi(acquired.doi):
            raise AssertionError("全文准入校验后 DOI 必须存在且一致")

        return doi

    @staticmethod
    def _document_object_key(
        *,
        collection_id: UUID,
        document_id: UUID,
        sha256: str,
    ) -> str:
        """正式对象按工作区、文档和内容哈希隔离，避免与暂存键混用。"""
        return f"documents/{collection_id}/{document_id}/{sha256}.pdf"

    @staticmethod
    def _build_paper(
        *,
        candidate: UnifiedCandidate,
        citation: CitationMetadata,
        doi: str,
    ) -> Paper:
        """将格式中立题录映射为唯一的长期论文记录。"""
        assert citation.issued_date is not None
        paper_type = normalize_document_type(citation.document_type)
        citation_provider = citation.field_provenance.get("doi", "doi_content_negotiation")

        return Paper(
            id=uuid4(),
            doi=doi,
            title=citation.title,
            authors=[author.to_csl_json() for author in citation.authors],
            abstract=candidate.abstract,
            publication_year=citation.issued_date.year,
            publication_month=citation.issued_date.month,
            publication_day=citation.issued_date.day,
            venue=citation.venue,
            paper_type=paper_type,
            volume=citation.volume,
            issue=citation.issue,
            pages=citation.pages,
            article_number=citation.article_number,
            publisher=citation.publisher,
            official_url=candidate.links.landing_url or candidate.links.open_access_url,
            language=None,
            citation_text=format_citation(citation, CitationFormat.GB_T_7714_2015_NUMERIC),
            citation_provider=citation_provider[:64] or "doi_content_negotiation",
            citation_source_url=citation.url,
        )

    async def _discard_staging_object(self, staging_object_key: str) -> None:
        """重复请求不再保留新的暂存副本，防止同一论文积累孤立对象。"""
        try:
            await self._storage.delete_object(object_key=staging_object_key)
        except FulltextStorageError as exc:
            raise CollectionAdmissionError(
                CollectionAdmissionErrorCode.STORAGE_ERROR,
                "无法清理重复请求的暂存全文，请稍后重试。",
                retryable=True,
            ) from exc

    async def _delete_promoted_object(self, document_object_key: str) -> None:
        """并发幂等命中时撤销刚复制的正式对象，避免产生孤立文件。"""
        try:
            await self._storage.delete_object(object_key=document_object_key)
        except FulltextStorageError as exc:
            raise CollectionAdmissionError(
                CollectionAdmissionErrorCode.STORAGE_ERROR,
                "重复加入后无法清理多余的正式全文对象。",
                retryable=True,
            ) from exc

    async def _cleanup_failed_admission(
        self,
        *,
        staging_object_key: str,
        document_object_key: str,
        original_error: Exception,
    ) -> None:
        """补偿失败流程可能遗留的正式或暂存对象，并保留原始失败原因。"""
        cleanup_errors: list[Exception] = []

        for object_key in (document_object_key, staging_object_key):
            try:
                await self._storage.delete_object(object_key=object_key)
            except FulltextStorageError as exc:
                cleanup_errors.append(exc)

        for cleanup_error in cleanup_errors:
            original_error.add_note(f"对象清理失败：{cleanup_error}")
