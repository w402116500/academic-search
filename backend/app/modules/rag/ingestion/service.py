"""按 parse、chunk、embed、index 阶段编排单篇文献的 RAG 入库。"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from app.modules.documents.storage import (
    FulltextStorageError,
    ReadableResearchDocumentObjectStorage,
)
from app.modules.rag.ingestion.chunking import HierarchicalChunker
from app.modules.rag.ingestion.contracts import (
    EmbeddedVectorChunk,
    IngestionError,
    IngestionErrorCode,
    IngestionOutcome,
)
from app.modules.rag.ingestion.embedding import TextEmbedder
from app.modules.rag.ingestion.parser import PdfTextParser
from app.modules.rag.ingestion.repository import IngestionRepository
from app.modules.rag.ingestion.vector_index import DocumentChunkVectorIndex


class DocumentIngestionService:
    """把已经准入的私有 PDF 处理为当前可检索的 L3 向量版本。"""

    def __init__(
        self,
        *,
        repository: IngestionRepository,
        storage: ReadableResearchDocumentObjectStorage,
        parser: PdfTextParser,
        chunker: HierarchicalChunker,
        embedder: TextEmbedder,
        vector_index: DocumentChunkVectorIndex,
        embedding_config: dict[str, str | int],
    ) -> None:
        """注入全部边界依赖，使编排器可由单元测试使用内存替身验证。"""
        self._repository = repository
        self._storage = storage
        self._parser = parser
        self._chunker = chunker
        self._embedder = embedder
        self._vector_index = vector_index
        self._embedding_config = embedding_config

    async def run(self, ingestion_run_id: UUID) -> IngestionOutcome:
        """执行一次可恢复入库；任何阶段失败都会回写 ``failed`` 状态。"""
        context = await self._repository.claim(ingestion_run_id)
        if context is None:
            return IngestionOutcome(
                ingestion_run_id=ingestion_run_id,
                status="completed",
                indexed_l3_chunk_count=0,
            )

        vectors_written = False
        try:
            if context.retrying:
                # 上次失败可能发生在补偿删除之后，先清理同一运行残留的旧向量再重新生成块。
                await self._vector_index.delete_ingestion_run(ingestion_run_id)

            with TemporaryDirectory(prefix="academic-search-ingestion-") as directory:
                pdf_path = Path(directory) / "document.pdf"
                await self._download_pdf(context.object_key, pdf_path)
                parsed = await self._parser.parse(pdf_path)
                await self._repository.record_parse(ingestion_run_id, parsed)

                chunks = self._chunker.build(parsed)
                await self._repository.replace_chunks(
                    ingestion_run_id,
                    chunks,
                    self._chunker.config.as_dict(),
                )
                vector_chunks = await self._repository.load_vector_chunks(context)
                if not vector_chunks:
                    raise IngestionError(
                        IngestionErrorCode.CHUNKING_FAILED,
                        "切块完成后没有可嵌入的 L3 片段。",
                    )

                vectors = await self._embedder.embed_documents(
                    tuple(chunk.content for chunk in vector_chunks)
                )
                if len(vectors) != len(vector_chunks):
                    raise IngestionError(
                        IngestionErrorCode.EMBEDDING_MISMATCH,
                        "嵌入模型返回的向量数量与 L3 片段数量不一致。",
                        retryable=True,
                    )

                embedded_chunks = tuple(
                    EmbeddedVectorChunk(chunk=chunk, embedding=vector)
                    for chunk, vector in zip(vector_chunks, vectors, strict=True)
                )
                vector_dimension = len(embedded_chunks[0].embedding)
                await self._repository.record_embedding(
                    ingestion_run_id,
                    self._embedding_config,
                    vector_dimension,
                )
                await self._vector_index.upsert(embedded_chunks)
                vectors_written = True
                await self._repository.complete(ingestion_run_id, len(embedded_chunks))

                return IngestionOutcome(
                    ingestion_run_id=ingestion_run_id,
                    status="completed",
                    indexed_l3_chunk_count=len(embedded_chunks),
                )
        except IngestionError as error:
            await self._mark_failed(ingestion_run_id, error, vectors_written)
            raise
        except FulltextStorageError as exc:
            error = IngestionError(
                IngestionErrorCode.STORAGE_READ_FAILED,
                "无法从私有对象存储读取已准入的 PDF。",
                retryable=True,
            )
            await self._mark_failed(ingestion_run_id, error, vectors_written)
            raise error from exc
        except Exception as exc:
            error = IngestionError(
                IngestionErrorCode.UNEXPECTED,
                "文献入库发生未预期错误，任务已停止。",
                retryable=True,
            )
            await self._mark_failed(ingestion_run_id, error, vectors_written)
            raise error from exc

    async def _download_pdf(self, object_key: str, destination: Path) -> None:
        """从正式对象键流式下载 PDF；临时目录会在本次任务结束后删除。"""
        await self._storage.download_object_to_file(object_key=object_key, destination=destination)

    async def _mark_failed(
        self,
        ingestion_run_id: UUID,
        error: IngestionError,
        vectors_written: bool,
    ) -> None:
        """先补偿已写入向量，再固化失败状态；补偿失败不会被静默忽略。"""
        errors: list[Exception] = [error]

        if vectors_written:
            try:
                await self._vector_index.delete_ingestion_run(ingestion_run_id)
            except Exception as exc:
                errors.append(exc)

        try:
            await self._repository.mark_failed(ingestion_run_id, error)
        except Exception as exc:
            errors.append(exc)

        if len(errors) > 1:
            raise ExceptionGroup("文献入库失败，且部分补偿或状态回写未完成", errors)
