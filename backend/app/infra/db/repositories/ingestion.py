"""RAG 入库状态和分块在 PostgreSQL 中的持久化实现。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from functools import wraps
from types import CoroutineType
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.collection import ResearchCollection
from app.infra.db.models.document import Document, DocumentChunk, IngestionRun
from app.modules.rag.ingestion.contracts import (
    DocumentChunkDraft,
    IngestionContext,
    IngestionError,
    IngestionErrorCode,
    ParsedDocument,
    VectorChunk,
)


def _map_persistence_errors[**Params, Result](
    method: Callable[Params, CoroutineType[Any, Any, Result]],
) -> Callable[Params, CoroutineType[Any, Any, Result]]:
    """Keep SQLAlchemy failures behind the ingestion persistence port."""

    @wraps(method)
    async def wrapped(*args: Params.args, **kwargs: Params.kwargs) -> Result:
        try:
            return await method(*args, **kwargs)
        except IngestionError:
            raise
        except SQLAlchemyError as exc:
            raise IngestionError(
                IngestionErrorCode.PERSISTENCE_FAILED,
                "入库状态或片段无法保存到 PostgreSQL。",
                retryable=True,
            ) from exc

    return wrapped


class SqlAlchemyIngestionRepository:
    """以短事务推进入库阶段，外部 I/O 不会长期占用 PostgreSQL 锁。"""

    def __init__(self, session: AsyncSession) -> None:
        """使用 Worker 生命周期内的异步会话，不创建隐藏的数据库连接。"""
        self._session = session

    @_map_persistence_errors
    async def claim(self, ingestion_run_id: UUID) -> IngestionContext | None:
        """锁定入库运行后领取任务，防止多个 arq Job 并发处理同一版本。"""
        async with self._session.begin():
            row = (
                await self._session.execute(
                    select(IngestionRun, Document, ResearchCollection)
                    .join(Document, IngestionRun.document_id == Document.id)
                    .join(ResearchCollection, Document.collection_id == ResearchCollection.id)
                    .where(IngestionRun.id == ingestion_run_id)
                    .with_for_update(of=IngestionRun)
                )
            ).one_or_none()

            if row is None:
                raise IngestionError(
                    IngestionErrorCode.INGESTION_RUN_NOT_FOUND,
                    "入库运行不存在或其文献已被删除。",
                )

            run, document, collection = row
            if run.status == "completed":
                return None
            if run.status == "running":
                raise IngestionError(
                    IngestionErrorCode.INGESTION_ALREADY_RUNNING,
                    "该文献入库任务已被其他 Worker 领取。",
                    retryable=True,
                )
            if collection.status != "active":
                error = IngestionError(
                    IngestionErrorCode.COLLECTION_UNAVAILABLE,
                    "研究工作区已不可用，不能继续处理其文献。",
                )
                run.status = "failed"
                run.is_current = False
                run.error_code = error.code.value
                run.error_message = str(error)
                run.finished_at = datetime.now(UTC)
                raise error
            if run.status not in {"queued", "failed"}:
                raise IngestionError(
                    IngestionErrorCode.PERSISTENCE_FAILED,
                    "入库运行不处于可领取状态。",
                )

            retrying = run.status == "failed"
            run.status = "running"
            run.stage = "parse"
            run.error_code = None
            run.error_message = None
            run.started_at = datetime.now(UTC)
            run.finished_at = None
            run.is_current = False

            return IngestionContext(
                ingestion_run_id=run.id,
                owner_user_id=collection.owner_user_id,
                collection_id=collection.id,
                document_id=document.id,
                object_key=document.object_key,
                retrying=retrying,
            )

    @_map_persistence_errors
    async def record_parse(self, ingestion_run_id: UUID, parsed: ParsedDocument) -> None:
        """持久化页面数量、空白页和解析器版本，为质量提示提供事实来源。"""
        async with self._session.begin():
            run = await self._locked_running_run(ingestion_run_id)
            run.parser_name = parsed.parser_name
            run.parser_version = parsed.parser_version
            run.statistics = {**run.statistics, **parsed.statistics()}
            run.stage = "chunk"

    @_map_persistence_errors
    async def replace_chunks(
        self,
        ingestion_run_id: UUID,
        chunks: Sequence[DocumentChunkDraft],
        chunking_config: dict[str, int | str],
    ) -> None:
        """替换同次运行的块，确保重试不会混入上一次失败留下的结果。"""
        if not chunks or not any(chunk.level == 3 for chunk in chunks):
            raise IngestionError(
                IngestionErrorCode.CHUNKING_FAILED,
                "切块结果为空或未产生可检索的 L3 片段。",
            )

        async with self._session.begin():
            run = await self._locked_running_run(ingestion_run_id)
            await self._session.execute(
                delete(DocumentChunk).where(DocumentChunk.ingestion_run_id == ingestion_run_id)
            )
            self._session.add_all(
                [
                    DocumentChunk(
                        id=chunk.id,
                        ingestion_run_id=ingestion_run_id,
                        parent_chunk_id=chunk.parent_chunk_id,
                        root_chunk_id=chunk.root_chunk_id,
                        level=chunk.level,
                        ordinal=chunk.ordinal,
                        content=chunk.content,
                        token_count=chunk.token_count,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        section_path=list(chunk.section_path) if chunk.section_path else None,
                        locator=chunk.locator,
                        content_sha256=chunk.content_sha256,
                    )
                    for chunk in chunks
                ]
            )
            run.chunking_config = dict(chunking_config)
            run.statistics = {
                **run.statistics,
                "l1_chunk_count": sum(chunk.level == 1 for chunk in chunks),
                "l2_chunk_count": sum(chunk.level == 2 for chunk in chunks),
                "l3_chunk_count": sum(chunk.level == 3 for chunk in chunks),
            }
            run.stage = "embed"

    @_map_persistence_errors
    async def load_vector_chunks(self, context: IngestionContext) -> tuple[VectorChunk, ...]:
        """按稳定 ordinal 读取 L3 原文；Milvus 不保存原文副本。

        SQLAlchemy 的首次查询会自动开启事务。这里会先把 ORM 结果转换为
        与会话脱钩的值对象，再结束只读事务，避免后续阶段开启短写事务时报
        ``A transaction is already begun on this Session``。
        """
        result = await self._session.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.ingestion_run_id == context.ingestion_run_id,
                DocumentChunk.level == 3,
            )
            .order_by(DocumentChunk.ordinal)
        )
        chunks = tuple(
            VectorChunk(
                chunk_id=chunk.id,
                owner_user_id=context.owner_user_id,
                collection_id=context.collection_id,
                document_id=context.document_id,
                ingestion_run_id=context.ingestion_run_id,
                level=chunk.level,
                content=chunk.content,
            )
            for chunk in result
        )
        # ``VectorChunk`` 不再依赖 ORM 对象，因此可安全关闭本次查询隐式开启的事务。
        await self._session.rollback()
        return chunks

    @_map_persistence_errors
    async def record_embedding(
        self,
        ingestion_run_id: UUID,
        embedding_config: dict[str, str | int],
        vector_dimension: int,
    ) -> None:
        """在向量写入前固化模型配置和真实维度，方便复现与排查。"""
        async with self._session.begin():
            run = await self._locked_running_run(ingestion_run_id)
            run.embedding_config = {**embedding_config, "vector_dimension": vector_dimension}
            run.statistics = {**run.statistics, "vector_dimension": vector_dimension}
            run.stage = "index"

    @_map_persistence_errors
    async def complete(self, ingestion_run_id: UUID, indexed_l3_chunk_count: int) -> None:
        """完成后才撤销旧 current 版本，避免新向量尚未写入时出现检索空窗。"""
        async with self._session.begin():
            run = await self._locked_running_run(ingestion_run_id)
            await self._session.execute(
                update(IngestionRun)
                .where(
                    IngestionRun.document_id == run.document_id,
                    IngestionRun.is_current.is_(True),
                    IngestionRun.id != ingestion_run_id,
                )
                .values(is_current=False)
            )
            run.status = "completed"
            run.stage = "index"
            run.is_current = True
            run.finished_at = datetime.now(UTC)
            run.statistics = {
                **run.statistics,
                "indexed_l3_chunk_count": indexed_l3_chunk_count,
            }

    @_map_persistence_errors
    async def mark_failed(self, ingestion_run_id: UUID, error: IngestionError) -> None:
        """保存失败代码和用户可读提示；失败运行永远不能成为 current。"""
        async with self._session.begin():
            run = await self._session.scalar(
                select(IngestionRun).where(IngestionRun.id == ingestion_run_id).with_for_update()
            )
            if run is None:
                return
            run.status = "failed"
            run.is_current = False
            run.error_code = error.code.value
            run.error_message = str(error)
            run.finished_at = datetime.now(UTC)

    async def _locked_running_run(self, ingestion_run_id: UUID) -> IngestionRun:
        """获取已领取的运行，阻止阶段跳跃或其他任务覆盖其状态。"""
        run = await self._session.scalar(
            select(IngestionRun).where(IngestionRun.id == ingestion_run_id).with_for_update()
        )
        if run is None:
            raise IngestionError(
                IngestionErrorCode.INGESTION_RUN_NOT_FOUND,
                "入库运行不存在或其文献已被删除。",
            )
        if run.status != "running":
            raise IngestionError(
                IngestionErrorCode.PERSISTENCE_FAILED,
                "入库运行状态已变化，不能继续写入当前阶段结果。",
                retryable=True,
            )
        return run
