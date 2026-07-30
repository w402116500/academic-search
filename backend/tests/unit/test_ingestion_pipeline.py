"""PDF 入库解析、三级分块和失败补偿的离线回归测试。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from app.modules.fulltext.storage import ReadableResearchDocumentObjectStorage
from app.modules.ingestion.chunking import ChunkingConfig, HierarchicalChunker
from app.modules.ingestion.contracts import (
    DocumentChunkDraft,
    EmbeddedVectorChunk,
    IngestionContext,
    IngestionError,
    IngestionErrorCode,
    ParsedDocument,
    ParsedPage,
    VectorChunk,
)
from app.modules.ingestion.embedding import TextEmbedder
from app.modules.ingestion.milvus import DocumentChunkVectorIndex
from app.modules.ingestion.parser import PdfTextParser
from app.modules.ingestion.repository import IngestionRepository
from app.modules.ingestion.service import DocumentIngestionService
from pypdf import PdfWriter

_RUN_ID = UUID("00000000-0000-0000-0000-000000000201")
_OWNER_ID = UUID("00000000-0000-0000-0000-000000000202")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000203")
_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000204")


def _parsed_document() -> ParsedDocument:
    """构造带多段文本的解析结果，避免单元测试依赖真实 PDF 下载。"""
    return ParsedDocument(
        parser_name="test-parser",
        parser_version="1.0",
        total_pages=2,
        pages=(
            ParsedPage(
                page_number=1,
                text=(
                    "Introduction explains the research problem and motivation.\n\n"
                    "Method describes the dataset, measurements, and analysis procedure."
                ),
            ),
            ParsedPage(
                page_number=2,
                text=(
                    "Results report the main quantitative findings with confidence intervals.\n\n"
                    "Discussion explains limitations and the implications for future work."
                ),
            ),
        ),
        empty_page_numbers=(),
    )


def test_hierarchical_chunker_creates_l1_l2_l3_lineage_and_page_locators() -> None:
    """所有叶子块应能向上回查 L2、L1 与 PDF 页码。"""
    chunker = HierarchicalChunker(
        ChunkingConfig(
            max_l1_characters=120,
            max_l2_characters=70,
            max_l3_characters=36,
            l3_overlap_characters=8,
        )
    )
    chunks = chunker.build(_parsed_document())
    by_id = {chunk.id: chunk for chunk in chunks}
    l1_chunks = [chunk for chunk in chunks if chunk.level == 1]
    l2_chunks = [chunk for chunk in chunks if chunk.level == 2]
    l3_chunks = [chunk for chunk in chunks if chunk.level == 3]

    assert l1_chunks and l2_chunks and l3_chunks
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.root_chunk_id == chunk.id for chunk in l1_chunks)
    assert all(chunk.token_count > 0 for chunk in chunks)
    assert all(chunk.page_start <= chunk.page_end for chunk in chunks)
    assert all(len(chunk.content) <= 36 for chunk in l3_chunks)

    for l2 in l2_chunks:
        assert l2.parent_chunk_id is not None
        assert l2.parent_chunk_id in by_id
        assert by_id[l2.parent_chunk_id].level == 1
        assert l2.root_chunk_id == l2.parent_chunk_id

    for l3 in l3_chunks:
        assert l3.parent_chunk_id is not None
        assert l3.parent_chunk_id in by_id
        assert by_id[l3.parent_chunk_id].level == 2
        assert by_id[l3.root_chunk_id].level == 1
        assert l3.locator["page_start"] == l3.page_start
        assert l3.locator["page_end"] == l3.page_end
        assert len(l3.content_sha256) == 64


def test_pdf_parser_explicitly_rejects_pdf_without_extractable_text(tmp_path: Path) -> None:
    """纯扫描件或空白 PDF 不能被标记为可用于 RAG 的成功解析结果。"""
    pdf_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with pdf_path.open("wb") as file:
        writer.write(file)

    with pytest.raises(IngestionError) as raised:
        PdfTextParser()._parse_sync(pdf_path)

    assert raised.value.code is IngestionErrorCode.PDF_NO_EXTRACTABLE_TEXT


class MemoryStorage:
    """将正式对象下载模拟为临时文件写入，避免测试连接 MinIO。"""

    async def download_object_to_file(self, *, object_key: str, destination: Path) -> None:
        assert object_key == "documents/collection/document/paper.pdf"
        destination.write_bytes(b"test pdf bytes")


class FixedParser:
    """返回固定解析结果，用于聚焦验证 Worker 编排。"""

    async def parse(self, file_path: Path) -> ParsedDocument:
        assert file_path.exists()
        return _parsed_document()


class MemoryRepository:
    """记录阶段写入和块数据的内存仓储。"""

    def __init__(self, *, retrying: bool = False) -> None:
        self.context = IngestionContext(
            ingestion_run_id=_RUN_ID,
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            document_id=_DOCUMENT_ID,
            object_key="documents/collection/document/paper.pdf",
            retrying=retrying,
        )
        self.operations: list[str] = []
        self.chunks: tuple[DocumentChunkDraft, ...] = ()
        self.failed: IngestionError | None = None

    async def claim(self, ingestion_run_id: UUID) -> IngestionContext | None:
        assert ingestion_run_id == _RUN_ID
        self.operations.append("claim")
        return self.context

    async def record_parse(self, ingestion_run_id: UUID, parsed: ParsedDocument) -> None:
        assert ingestion_run_id == _RUN_ID
        assert parsed.text_page_count == 2
        self.operations.append("parse")

    async def replace_chunks(
        self,
        ingestion_run_id: UUID,
        chunks: Sequence[DocumentChunkDraft],
        chunking_config: dict[str, int | str],
    ) -> None:
        assert ingestion_run_id == _RUN_ID
        assert chunking_config["max_l3_characters"] == 36
        self.chunks = tuple(chunks)
        self.operations.append("chunk")

    async def load_vector_chunks(self, context: IngestionContext) -> tuple[VectorChunk, ...]:
        assert context == self.context
        self.operations.append("load_l3")
        return tuple(
            VectorChunk(
                chunk_id=chunk.id,
                owner_user_id=context.owner_user_id,
                collection_id=context.collection_id,
                document_id=context.document_id,
                ingestion_run_id=context.ingestion_run_id,
                level=chunk.level,
                content=chunk.content,
            )
            for chunk in self.chunks
            if chunk.level == 3
        )

    async def record_embedding(
        self,
        ingestion_run_id: UUID,
        embedding_config: dict[str, str | int],
        vector_dimension: int,
    ) -> None:
        assert ingestion_run_id == _RUN_ID
        assert embedding_config["model"] == "test-embedding"
        assert vector_dimension == 3
        self.operations.append("embed")

    async def complete(self, ingestion_run_id: UUID, indexed_l3_chunk_count: int) -> None:
        assert ingestion_run_id == _RUN_ID
        assert indexed_l3_chunk_count > 0
        self.operations.append("complete")

    async def mark_failed(self, ingestion_run_id: UUID, error: IngestionError) -> None:
        assert ingestion_run_id == _RUN_ID
        self.failed = error
        self.operations.append("failed")


class FixedEmbedder:
    """为每段文本返回三维向量，便于断言 L3-only 写入。"""

    async def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple((float(index), 0.5, 1.0) for index, _ in enumerate(texts, start=1))


class MemoryVectorIndex:
    """记录写入和补偿删除的内存 Milvus 替身。"""

    def __init__(self) -> None:
        self.upserts: tuple[EmbeddedVectorChunk, ...] = ()
        self.deleted_run_ids: list[UUID] = []

    async def upsert(self, chunks: Sequence[EmbeddedVectorChunk]) -> None:
        self.upserts = tuple(chunks)

    async def delete_ingestion_run(self, ingestion_run_id: UUID) -> None:
        self.deleted_run_ids.append(ingestion_run_id)


def _service(
    repository: MemoryRepository,
    vector_index: MemoryVectorIndex,
) -> DocumentIngestionService:
    """使用内存替身创建完整的 Worker 编排器。"""
    return DocumentIngestionService(
        repository=cast(IngestionRepository, repository),
        storage=cast(ReadableResearchDocumentObjectStorage, MemoryStorage()),
        parser=cast(PdfTextParser, FixedParser()),
        chunker=HierarchicalChunker(
            ChunkingConfig(
                max_l1_characters=120,
                max_l2_characters=70,
                max_l3_characters=36,
                l3_overlap_characters=8,
            )
        ),
        embedder=cast(TextEmbedder, FixedEmbedder()),
        vector_index=cast(DocumentChunkVectorIndex, vector_index),
        embedding_config={"model": "test-embedding"},
    )


@pytest.mark.asyncio
async def test_ingestion_service_persists_all_levels_and_indexes_only_l3() -> None:
    """成功链路应按阶段处理，并且写入向量库的片段全部是 L3。"""
    repository = MemoryRepository()
    vector_index = MemoryVectorIndex()

    outcome = await _service(repository, vector_index).run(_RUN_ID)

    assert outcome.status == "completed"
    assert outcome.indexed_l3_chunk_count == len(vector_index.upserts)
    assert repository.operations == ["claim", "parse", "chunk", "load_l3", "embed", "complete"]
    assert {chunk.level for chunk in repository.chunks} == {1, 2, 3}
    assert vector_index.upserts
    assert all(item.chunk.level == 3 for item in vector_index.upserts)
    assert not vector_index.deleted_run_ids
    assert repository.failed is None


@pytest.mark.asyncio
async def test_ingestion_service_cleans_a_failed_run_before_retrying() -> None:
    """重试前应先清除同一运行可能残留的旧向量，再写入新的 L3 结果。"""
    repository = MemoryRepository(retrying=True)
    vector_index = MemoryVectorIndex()

    await _service(repository, vector_index).run(_RUN_ID)

    assert vector_index.deleted_run_ids == [_RUN_ID]
    assert vector_index.upserts


class FailingCompleteRepository(MemoryRepository):
    """模拟 Milvus 已成功写入后 PostgreSQL 最终提交失败。"""

    async def complete(self, ingestion_run_id: UUID, indexed_l3_chunk_count: int) -> None:
        del ingestion_run_id, indexed_l3_chunk_count
        raise RuntimeError("forced completion failure")


@pytest.mark.asyncio
async def test_ingestion_service_deletes_vectors_when_final_state_persistence_fails() -> None:
    """向量写入成功但完成态失败时，必须执行精确的 Milvus 补偿删除。"""
    repository = FailingCompleteRepository()
    vector_index = MemoryVectorIndex()

    with pytest.raises(IngestionError) as raised:
        await _service(repository, vector_index).run(_RUN_ID)

    assert raised.value.code is IngestionErrorCode.UNEXPECTED
    assert vector_index.upserts
    assert vector_index.deleted_run_ids == [_RUN_ID]
    assert repository.failed is raised.value
