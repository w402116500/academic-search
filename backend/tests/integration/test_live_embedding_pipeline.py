"""硅基流动 OpenAI 兼容 embedding 与 Milvus 的真实连通性测试。"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from app.core.ingestion_settings import IngestionSettings, get_ingestion_settings
from app.infra.llm.embeddings import OpenAICompatibleTextEmbedder
from app.infra.milvus.document_chunks import MilvusDocumentChunkIndex
from app.modules.rag.ingestion.contracts import EmbeddedVectorChunk, VectorChunk
from pymilvus import MilvusClient

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_EMBEDDING_TESTS"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_embedding_provider_returns_vectors_accepted_by_milvus() -> None:
    """验证真实模型维度与临时 L3 向量写入，不保留测试 collection。"""
    if os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) != "1":
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实 embedding 验收")

    settings = get_ingestion_settings()
    texts = (
        "Academic RAG retrieves evidence from verified research papers.",
        "学术文献研究需要将结论关联到可定位的原文证据。",
    )
    vectors = await OpenAICompatibleTextEmbedder(settings).embed_documents(texts)
    dimension = len(vectors[0])

    assert len(vectors) == len(texts)
    assert dimension == 1_024
    assert all(len(vector) == dimension for vector in vectors)

    collection_name = f"academic_search_embedding_smoke_{uuid4().hex}"
    index_settings = SimpleNamespace(
        milvus_uri=settings.milvus_uri,
        milvus_token=settings.milvus_token,
        milvus_collection_name=collection_name,
    )
    client = MilvusClient(
        uri=settings.milvus_uri,
        token=settings.milvus_token.get_secret_value() if settings.milvus_token else "",
    )
    vector_index = MilvusDocumentChunkIndex(
        cast(IngestionSettings, index_settings),
        client=client,
    )
    run_id = uuid4()
    chunk = VectorChunk(
        chunk_id=uuid4(),
        owner_user_id=uuid4(),
        collection_id=uuid4(),
        document_id=uuid4(),
        ingestion_run_id=run_id,
        level=3,
        content=texts[0],
    )

    try:
        await vector_index.upsert((EmbeddedVectorChunk(chunk=chunk, embedding=vectors[0]),))
        records = await asyncio.to_thread(
            client.query,
            collection_name,
            filter=f'ingestion_run_id == "{run_id}"',
            output_fields=["chunk_id", "level"],
        )
        assert records == [{"chunk_id": str(chunk.chunk_id), "level": 3}]

        await vector_index.delete_ingestion_run(run_id)
        assert (
            await asyncio.to_thread(
                client.query,
                collection_name,
                filter=f'ingestion_run_id == "{run_id}"',
                output_fields=["chunk_id"],
            )
            == []
        )
    finally:
        if client.has_collection(collection_name=collection_name):
            client.drop_collection(collection_name=collection_name)
