"""真实开放获取 PDF 到私有对象存储暂存区的手动集成测试。

本测试会访问 arXiv，并在本地 MinIO/S3 bucket 中短暂创建一个对象；只有显式设置
``RUN_LIVE_FULLTEXT_TESTS=1`` 才会运行。测试完成后会删除它创建的唯一对象。
"""

from __future__ import annotations

import asyncio
import json
import os
from uuid import UUID, uuid4

import pytest
from app.core.fulltext_settings import get_fulltext_acquisition_settings
from app.infra.storage.documents import Boto3StagingObjectStorage
from app.modules.documents.acquisition import OpenAccessPdfAcquirer
from app.modules.documents.contracts import (
    FulltextAcquisitionStatus,
    FulltextCandidate,
    FulltextCandidateLinks,
)
from app.modules.literature.contracts import (
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
)
from botocore.exceptions import ClientError

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_FULLTEXT_TESTS"
_ARXIV_DOI = "10.48550/arXiv.1706.03762"
_ARXIV_PDF_URL = "https://arxiv.org/pdf/1706.03762"


def _live_test_is_enabled() -> bool:
    """只在用户显式允许时访问外部论文来源并创建临时对象。"""
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


def _candidate(candidate_id: UUID) -> FulltextCandidate:
    """构造一篇 DOI 已就绪、来源明确开放获取的真实论文候选。"""
    return FulltextCandidate(
        candidate_id=candidate_id,
        doi=_ARXIV_DOI,
        links=FulltextCandidateLinks(fulltext_url=_ARXIV_PDF_URL),
        is_open_access=True,
        citation=CitationMetadata(
            status=CitationMetadataStatus.READY,
            authors=(CitationAuthor(given="Ashish", family="Vaswani"),),
            title="Attention Is All You Need",
            document_type="article",
            issued_date=CitationDate(year=2017),
            venue="Advances in Neural Information Processing Systems",
            doi=_ARXIV_DOI,
            url="https://doi.org/10.48550/arXiv.1706.03762",
        ),
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_acquisition_downloads_stages_reads_and_deletes_pdf() -> None:
    """真实链路应下载、校验、暂存、读取元数据、列出并清理测试 PDF。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实全文获取测试")

    settings = get_fulltext_acquisition_settings()
    storage = Boto3StagingObjectStorage(settings)
    candidate_id = uuid4()
    result = await OpenAccessPdfAcquirer(settings, storage).acquire(_candidate(candidate_id))

    assert result.status is FulltextAcquisitionStatus.AVAILABLE, result.error
    assert result.document is not None

    object_key = result.document.staging_object_key
    expected_prefix = f"{settings.fulltext_staging_prefix}/{candidate_id}/"
    assert object_key.startswith(expected_prefix)

    try:
        metadata = await asyncio.to_thread(
            storage._client.head_object,
            Bucket=settings.s3_bucket,
            Key=object_key,
        )
        listed = await asyncio.to_thread(
            storage._client.list_objects_v2,
            Bucket=settings.s3_bucket,
            Prefix=object_key,
        )

        assert metadata["ContentLength"] == result.document.byte_size
        assert metadata["ContentType"] == "application/pdf"
        assert metadata["Metadata"].get("sha256") == result.document.sha256
        assert any(item["Key"] == object_key for item in listed.get("Contents", []))
        print(
            json.dumps(
                {
                    "source_url": result.document.source_url,
                    "staging_object_key": object_key,
                    "downloaded_byte_size": result.document.byte_size,
                    "stored_content_length": metadata["ContentLength"],
                    "sha256": result.document.sha256,
                    "stored_content_type": metadata["ContentType"],
                },
                ensure_ascii=True,
            )
        )
    finally:
        # 仅删除本次随机 candidate_id 生成的暂存对象，绝不清理整个 staging 前缀。
        await asyncio.to_thread(
            storage._client.delete_object,
            Bucket=settings.s3_bucket,
            Key=object_key,
        )

    with pytest.raises(ClientError):
        await asyncio.to_thread(
            storage._client.head_object,
            Bucket=settings.s3_bucket,
            Key=object_key,
        )

    print(json.dumps({"cleanup": "deleted", "staging_object_key": object_key}, ensure_ascii=True))
