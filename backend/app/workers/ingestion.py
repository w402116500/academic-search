"""RAG 文献入库的 arq Worker 配置与任务函数。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from app.core.fulltext_settings import get_fulltext_acquisition_settings
from app.core.ingestion_settings import IngestionSettings, get_ingestion_settings
from app.infra.db.repositories.collection_builds import SqlAlchemyCollectionBuildAdapter
from app.infra.db.repositories.ingestion import SqlAlchemyIngestionRepository
from app.infra.db.session import async_session_factory
from app.infra.llm.embeddings import OpenAICompatibleTextEmbedder
from app.infra.milvus.document_chunks import MilvusDocumentChunkIndex
from app.infra.redis.connection import redis_settings_from_environment
from app.infra.redis.queues import INGESTION_QUEUE_NAME
from app.infra.storage.documents import Boto3StagingObjectStorage
from app.modules.rag.ingestion.chunking import ChunkingConfig, HierarchicalChunker
from app.modules.rag.ingestion.contracts import IngestionError, IngestionErrorCode
from app.modules.rag.ingestion.parser import PdfTextParser
from app.modules.rag.ingestion.service import DocumentIngestionService


@dataclass(frozen=True, slots=True)
class WorkerDependencies:
    """Worker 进程启动时创建并跨 Job 复用的无状态基础设施适配器。"""

    settings: IngestionSettings
    storage: Boto3StagingObjectStorage
    parser: PdfTextParser
    chunker: HierarchicalChunker
    embedder: OpenAICompatibleTextEmbedder
    vector_index: MilvusDocumentChunkIndex


async def startup(ctx: dict[str, Any]) -> None:
    """初始化外部服务客户端；连接失败会在实际 Job 中以可见错误方式暴露。"""
    settings = get_ingestion_settings()
    ctx["ingestion_dependencies"] = WorkerDependencies(
        settings=settings,
        storage=Boto3StagingObjectStorage(get_fulltext_acquisition_settings()),
        parser=PdfTextParser(),
        chunker=HierarchicalChunker(
            ChunkingConfig(
                max_l1_characters=settings.rag_max_l1_characters,
                max_l2_characters=settings.rag_max_l2_characters,
                max_l3_characters=settings.rag_max_l3_characters,
                l3_overlap_characters=settings.rag_l3_overlap_characters,
                tokenizer_encoding=settings.rag_tokenizer_encoding,
            )
        ),
        embedder=OpenAICompatibleTextEmbedder(settings),
        vector_index=MilvusDocumentChunkIndex(settings),
    )


async def ingest_document(ctx: dict[str, Any], ingestion_run_id: str) -> dict[str, str | int]:
    """执行单次文献入库任务，并让 arq 根据抛出的异常决定是否重试。"""
    try:
        run_id = UUID(ingestion_run_id)
    except ValueError as exc:
        raise IngestionError(
            IngestionErrorCode.INVALID_TASK_PAYLOAD,
            "arq 入库任务缺少合法的 ingestion_run_id。",
        ) from exc

    dependencies = cast(WorkerDependencies, ctx["ingestion_dependencies"])
    try:
        try:
            async with async_session_factory() as session:
                outcome = await DocumentIngestionService(
                    repository=SqlAlchemyIngestionRepository(session),
                    storage=dependencies.storage,
                    parser=dependencies.parser,
                    chunker=dependencies.chunker,
                    embedder=dependencies.embedder,
                    vector_index=dependencies.vector_index,
                    embedding_config=dependencies.settings.embedding_snapshot,
                ).run(run_id)
        except IngestionError as exc:
            if exc.code is not IngestionErrorCode.CANCELLED:
                raise
            return {
                "ingestion_run_id": str(run_id),
                "status": "cancelled",
                "indexed_l3_chunk_count": 0,
            }
    finally:
        # 入库服务已先提交本次运行的 completed/failed 状态；这里再汇总工作区展示阶段。
        async with async_session_factory() as stage_session:
            await SqlAlchemyCollectionBuildAdapter(
                stage_session
            ).refresh_collection_stage_for_ingestion_run(run_id)

    return {
        "ingestion_run_id": str(outcome.ingestion_run_id),
        "status": outcome.status,
        "indexed_l3_chunk_count": outcome.indexed_l3_chunk_count,
    }


class WorkerSettings:
    """供 ``arq app.workers.ingestion.WorkerSettings`` 启动的 Worker 配置。"""

    functions = [ingest_document]
    on_startup = startup
    redis_settings = redis_settings_from_environment()
    queue_name = INGESTION_QUEUE_NAME
    max_jobs = 2
    max_tries = 3
    job_timeout = 900
