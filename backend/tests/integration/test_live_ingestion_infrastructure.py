"""本地 MinIO 与 Milvus 的 RAG 入库基础设施真实集成测试。"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from app.core.env import load_env
from app.core.fulltext_settings import get_fulltext_acquisition_settings
from app.core.ingestion_settings import IngestionSettings
from app.infra.milvus.document_chunks import MilvusDocumentChunkIndex
from app.infra.storage.documents import Boto3StagingObjectStorage
from app.modules.rag.ingestion.contracts import EmbeddedVectorChunk, VectorChunk
from pymilvus import MilvusClient

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_INGESTION_INFRASTRUCTURE_TESTS"


def _live_tests_enabled() -> bool:
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


@pytest.fixture(autouse=True)
def require_live_ingestion_infrastructure() -> None:
    """本文件触碰 MinIO 和 Milvus，必须显式打开真实基础设施验收开关。"""
    if not _live_tests_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实入库基础设施测试")


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_minio_downloads_a_formal_document_object_to_a_temporary_file(
    tmp_path: Path,
) -> None:
    """Worker 应能从正式对象键流式读取已准入文件，而非依赖本地持久目录。"""
    storage = Boto3StagingObjectStorage(get_fulltext_acquisition_settings())
    payload = b"%PDF-1.7\nRAG ingestion storage smoke test\n"
    object_key = f"tests/ingestion/{uuid4()}.pdf"
    source_path = tmp_path / "source.pdf"
    downloaded_path = tmp_path / "downloaded.pdf"
    source_path.write_bytes(payload)

    try:
        with source_path.open("rb") as source_file:
            await storage.upload_pdf(
                object_key=object_key,
                file=source_file,
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        await storage.download_object_to_file(
            object_key=object_key,
            destination=downloaded_path,
        )
        assert downloaded_path.read_bytes() == payload
    finally:
        await storage.delete_object(object_key=object_key)


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_milvus_writes_and_compensates_a_single_l3_vector() -> None:
    """Milvus schema 必须接受隔离字段与 L3 向量，并支持按运行 ID 精确删除。"""
    load_env()
    settings = SimpleNamespace(
        milvus_uri=os.environ["MILVUS_URI"],
        milvus_token=None,
        milvus_collection_name=f"academic_search_ingestion_test_{uuid4().hex}",
    )
    client = MilvusClient(uri=settings.milvus_uri)
    vector_index = MilvusDocumentChunkIndex(cast(IngestionSettings, settings), client=client)
    run_id = uuid4()
    vector = EmbeddedVectorChunk(
        chunk=VectorChunk(
            chunk_id=uuid4(),
            owner_user_id=uuid4(),
            collection_id=uuid4(),
            document_id=uuid4(),
            ingestion_run_id=run_id,
            level=3,
            content="Milvus L3 vector smoke test.",
        ),
        embedding=(0.1, 0.2, 0.3, 0.4),
    )

    try:
        await vector_index.upsert((vector,))
        records = await asyncio.to_thread(
            client.query,
            settings.milvus_collection_name,
            filter=f'ingestion_run_id == "{run_id}"',
            output_fields=["chunk_id", "level", "collection_id"],
        )
        assert records == [
            {
                "chunk_id": str(vector.chunk.chunk_id),
                "level": 3,
                "collection_id": str(vector.chunk.collection_id),
            }
        ]

        await vector_index.delete_ingestion_run(run_id)
        assert (
            await asyncio.to_thread(
                client.query,
                settings.milvus_collection_name,
                filter=f'ingestion_run_id == "{run_id}"',
                output_fields=["chunk_id"],
            )
            == []
        )
    finally:
        if client.has_collection(collection_name=settings.milvus_collection_name):
            client.drop_collection(collection_name=settings.milvus_collection_name)
