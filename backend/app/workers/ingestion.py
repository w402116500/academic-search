"""RAG 文献入库的 arq Worker 配置与任务函数。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

from arq.connections import RedisSettings

from app.db.session import async_session_factory
from app.modules.fulltext.settings import get_fulltext_acquisition_settings
from app.modules.fulltext.storage import Boto3StagingObjectStorage
from app.modules.ingestion.chunking import ChunkingConfig, HierarchicalChunker
from app.modules.ingestion.contracts import IngestionError, IngestionErrorCode
from app.modules.ingestion.embedding import OpenAICompatibleTextEmbedder
from app.modules.ingestion.milvus import MilvusDocumentChunkIndex
from app.modules.ingestion.parser import PdfTextParser
from app.modules.ingestion.repository import SqlAlchemyIngestionRepository
from app.modules.ingestion.service import DocumentIngestionService
from app.modules.ingestion.settings import IngestionSettings, get_ingestion_settings


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

    return {
        "ingestion_run_id": str(outcome.ingestion_run_id),
        "status": outcome.status,
        "indexed_l3_chunk_count": outcome.indexed_l3_chunk_count,
    }


def _redis_settings_from_environment() -> RedisSettings:
    """从唯一的 ``REDIS_URL`` 解析 arq 连接配置，避免维护第二套 Redis 地址。"""
    raw_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"redis", "rediss"} or parsed.hostname is None:
        raise RuntimeError("REDIS_URL 必须是完整的 redis:// 或 rediss:// 地址")

    database_text = parsed.path.strip("/") or "0"
    if not database_text.isdecimal():
        raise RuntimeError("REDIS_URL 的数据库编号必须是非负整数")

    # Docker Desktop 的 Redis 默认仅发布 IPv4；Windows 的 localhost 会优先解析到 ::1。
    host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
    return RedisSettings(
        host=host,
        port=parsed.port or 6379,
        database=int(database_text),
        username=parsed.username,
        password=parsed.password,
        ssl=parsed.scheme == "rediss",
    )


class WorkerSettings:
    """供 ``arq app.workers.ingestion.WorkerSettings`` 启动的 Worker 配置。"""

    functions = [ingest_document]
    on_startup = startup
    redis_settings = _redis_settings_from_environment()
    max_jobs = 2
    max_tries = 3
    job_timeout = 900
