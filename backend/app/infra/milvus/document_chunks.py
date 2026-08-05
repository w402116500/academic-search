"""只存储 L3 向量与检索过滤字段的 Milvus 适配器。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from pymilvus import DataType, MilvusClient

from app.core.ingestion_settings import IngestionSettings
from app.modules.rag.ingestion.contracts import (
    EmbeddedVectorChunk,
    IngestionError,
    IngestionErrorCode,
)


class MilvusDocumentChunkIndex:
    """为当前项目创建并写入固定 L3 向量集合。"""

    def __init__(self, settings: IngestionSettings, *, client: MilvusClient | None = None) -> None:
        """允许测试注入内存替身，生产环境只在 Worker 启动时创建一个客户端。"""
        self._collection_name = settings.milvus_collection_name
        self._client = client or MilvusClient(
            uri=settings.milvus_uri,
            token=(settings.milvus_token.get_secret_value() if settings.milvus_token else ""),
        )

    async def upsert(self, chunks: Sequence[EmbeddedVectorChunk]) -> None:
        """确保集合存在后批量写入 L3 向量及工作区过滤字段。"""
        if not chunks:
            return
        if any(chunk.chunk.level != 3 for chunk in chunks):
            raise IngestionError(
                IngestionErrorCode.VECTOR_INDEX_FAILED,
                "Milvus 只允许写入 L3 叶子片段。",
            )

        try:
            await asyncio.to_thread(self._upsert_sync, chunks)
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError(
                IngestionErrorCode.VECTOR_INDEX_FAILED,
                "Milvus 向量写入失败，当前文献不会标记为可检索。",
                retryable=True,
            ) from exc

    async def delete_ingestion_run(self, ingestion_run_id: UUID) -> None:
        """删除本次运行所有向量，避免 PostgreSQL 提交失败后残留不可追溯索引。"""
        try:
            await asyncio.to_thread(self._delete_ingestion_run_sync, ingestion_run_id)
        except Exception as exc:
            raise IngestionError(
                IngestionErrorCode.VECTOR_INDEX_FAILED,
                "Milvus 失败补偿未完成，需要人工检查残留向量。",
                retryable=True,
            ) from exc

    def _upsert_sync(self, chunks: Sequence[EmbeddedVectorChunk]) -> None:
        """同步 Milvus SDK 调用；向量维度由第一条真实 embedding 决定。"""
        dimension = len(chunks[0].embedding)
        if dimension == 0 or any(len(chunk.embedding) != dimension for chunk in chunks):
            raise IngestionError(
                IngestionErrorCode.EMBEDDING_MISMATCH,
                "写入 Milvus 前发现向量为空或维度不一致。",
            )

        self._ensure_collection(dimension)
        self._client.upsert(
            collection_name=self._collection_name,
            data=[
                {
                    "chunk_id": str(item.chunk.chunk_id),
                    "owner_user_id": str(item.chunk.owner_user_id),
                    "collection_id": str(item.chunk.collection_id),
                    "document_id": str(item.chunk.document_id),
                    "ingestion_run_id": str(item.chunk.ingestion_run_id),
                    "level": item.chunk.level,
                    "embedding": list(item.embedding),
                }
                for item in chunks
            ],
        )
        # Milvus 写入默认是最终一致的；入库完成后必须刷新，保证后续检索
        # 能立即看到刚提交的文献片段，而不是在短暂窗口内返回空结果。
        self._client.flush(collection_name=self._collection_name)

    def _ensure_collection(self, dimension: int) -> None:
        """首次写入时创建固定 schema；既有集合继续使用其原始向量维度。"""
        if self._client.has_collection(collection_name=self._collection_name):
            return

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=36)
        schema.add_field("owner_user_id", DataType.VARCHAR, max_length=36)
        schema.add_field("collection_id", DataType.VARCHAR, max_length=36)
        schema.add_field("document_id", DataType.VARCHAR, max_length=36)
        schema.add_field("ingestion_run_id", DataType.VARCHAR, max_length=36)
        schema.add_field("level", DataType.INT8)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dimension)

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        self._client.create_collection(
            collection_name=self._collection_name,
            schema=schema,
            index_params=index_params,
        )

    def _delete_ingestion_run_sync(self, ingestion_run_id: UUID) -> None:
        """按 UUID 精确删除一个运行的向量，不影响其他文献或版本。"""
        if not self._client.has_collection(collection_name=self._collection_name):
            return
        self._client.delete(
            collection_name=self._collection_name,
            filter=f'ingestion_run_id == "{ingestion_run_id}"',
        )
        # 删除在 Milvus 中默认异步可见；补偿路径必须等待落盘，不能留下短暂可召回的旧向量。
        self._client.flush(collection_name=self._collection_name)
