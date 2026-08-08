"""开放获取 PDF 全文获取模块的离线安全与完整性测试。"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
from io import BytesIO
from typing import BinaryIO
from uuid import UUID

import httpx
import pytest
from app.core.fulltext_settings import FulltextAcquisitionSettings
from app.core.settings import NetworkMode
from app.infra.storage.documents import Boto3StagingObjectStorage
from app.modules.documents.acquisition import (
    AuthorizedPdfUploader,
    OpenAccessPdfAcquirer,
    OpenAccessPdfAvailabilityProbe,
)
from app.modules.documents.contracts import (
    FulltextAcquisitionErrorCode,
    FulltextAcquisitionStatus,
    FulltextCandidate,
    FulltextCandidateLinks,
    PdfAvailabilityStatus,
)
from app.modules.documents.storage import FulltextStorageError
from app.modules.literature.contracts import (
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
)
from boto3.exceptions import S3UploadFailedError
from pydantic import SecretStr

_CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000123")
_PUBLIC_ADDRESS = ipaddress.ip_address("8.8.8.8")
_PRIVATE_ADDRESS = ipaddress.ip_address("127.0.0.1")
_PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"


class MemoryStorage:
    """记录上传内容的内存存储，用于断言下载器不依赖真实 MinIO。"""

    def __init__(self) -> None:
        self.uploads: dict[str, tuple[bytes, str]] = {}

    async def upload_pdf(self, *, object_key: str, file: BinaryIO, sha256: str) -> None:
        """读取当前文件内容，模拟私有对象上传完成。"""
        file.seek(0)
        self.uploads[object_key] = (file.read(), sha256)


class FailingStorage:
    """模拟私有对象存储不可用，验证调用方能获得可重试错误。"""

    async def upload_pdf(self, *, object_key: str, file: BinaryIO, sha256: str) -> None:
        """故意拒绝写入，避免测试依赖真实 S3 服务。"""
        raise FulltextStorageError("temporary storage outage")


class FailingBoto3Client:
    """模拟 boto3 传输层抛出的专用上传失败异常。"""

    def upload_fileobj(self, *args: object, **kwargs: object) -> None:
        """模拟 SDK 已开始上传后报告的失败。"""
        raise S3UploadFailedError("forced upload failure")


def _settings(
    *,
    fulltext_max_file_size_bytes: int = 52_428_800,
    fulltext_total_timeout_seconds: float = 90,
    fulltext_network_mode: NetworkMode = "direct",
    literature_proxy_url: str | None = None,
) -> FulltextAcquisitionSettings:
    """构建只用于测试的全文下载配置。"""
    return FulltextAcquisitionSettings(
        s3_endpoint_url="http://minio.example.test:9000",
        s3_region="us-east-1",
        s3_bucket="academic-documents",
        s3_access_key=SecretStr("test-access-key"),
        s3_secret_key=SecretStr("test-secret-key"),
        fulltext_max_file_size_bytes=fulltext_max_file_size_bytes,
        fulltext_total_timeout_seconds=fulltext_total_timeout_seconds,
        fulltext_network_mode=fulltext_network_mode,
        literature_proxy_url=literature_proxy_url,
    )


def _candidate(
    *,
    doi: str | None = "10.1000/fulltext.example",
    citation_doi: str | None = "10.1000/fulltext.example",
    citation_status: CitationMetadataStatus = CitationMetadataStatus.READY,
    is_open_access: bool | None = True,
    fulltext_url: str | None = "https://downloads.example.test/paper.pdf",
) -> FulltextCandidate:
    """构造具备最小正式题录的内部候选，不通过外部 API 生成测试数据。"""
    citation = CitationMetadata(
        status=citation_status,
        authors=(CitationAuthor(given="Ada", family="Lovelace"),),
        title="A verified open access paper",
        document_type="journal_article",
        issued_date=CitationDate(year=2024, month=1),
        venue="Journal of Tests",
        volume="1",
        pages="1-10",
        doi=citation_doi,
        url="https://doi.org/10.1000/fulltext.example",
    )
    return FulltextCandidate(
        candidate_id=_CANDIDATE_ID,
        doi=doi,
        links=FulltextCandidateLinks(fulltext_url=fulltext_url),
        is_open_access=is_open_access,
        citation=citation,
    )


async def _public_resolver(_host: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    """将测试域名稳定解析为公网地址，避免测试触发 DNS。"""
    return (_PUBLIC_ADDRESS,)


async def _private_resolver(
    _host: str,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    """模拟恶意链接解析到回环地址的场景。"""
    return (_PRIVATE_ADDRESS,)


async def _upload_chunks(*chunks: bytes):
    """以与 FastAPI Request.stream() 相同的异步字节流形状提供上传正文。"""
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_acquirer_downloads_valid_pdf_and_uploads_a_private_staging_object() -> None:
    """满足 DOI、开放获取与 PDF 校验后，才能产生可供后续入库的文件结果。"""
    storage = MemoryStorage()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://downloads.example.test/paper.pdf"
        assert request.headers["accept"] == "application/pdf"
        return httpx.Response(200, content=_PDF_BYTES, headers={"Content-Type": "application/pdf"})

    acquirer = OpenAccessPdfAcquirer(
        _settings(),
        storage,
        transport=httpx.MockTransport(handler),
        host_resolver=_public_resolver,
    )
    result = await acquirer.acquire(_candidate())

    assert result.status is FulltextAcquisitionStatus.AVAILABLE
    assert result.document is not None
    assert result.document.sha256 == hashlib.sha256(_PDF_BYTES).hexdigest()
    assert result.document.byte_size == len(_PDF_BYTES)
    assert result.document.staging_object_key in storage.uploads
    assert storage.uploads[result.document.staging_object_key][0] == _PDF_BYTES


@pytest.mark.asyncio
async def test_acquirer_requires_upload_when_candidate_is_not_open_access() -> None:
    """未经来源明确标注为开放获取的链接不能触发自动下载。"""
    storage = MemoryStorage()
    acquirer = OpenAccessPdfAcquirer(_settings(), storage, host_resolver=_public_resolver)
    result = await acquirer.acquire(_candidate(is_open_access=False))

    assert result.status is FulltextAcquisitionStatus.REQUIRES_UPLOAD
    assert result.error is not None
    assert result.error.code is FulltextAcquisitionErrorCode.NOT_OPEN_ACCESS
    assert not storage.uploads


@pytest.mark.asyncio
async def test_availability_probe_reports_available_without_storing_pdf() -> None:
    """筛选阶段只读取响应头确认 PDF 可得性，不下载或暂存文件。"""
    seen_methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_methods.append(request.method)
        assert request.url == "https://downloads.example.test/paper.pdf"
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf", "Content-Length": str(len(_PDF_BYTES))},
        )

    probe = OpenAccessPdfAvailabilityProbe(
        _settings(),
        transport=httpx.MockTransport(handler),
        host_resolver=_public_resolver,
    )
    result = await probe.probe(_candidate())

    assert result.status is PdfAvailabilityStatus.AVAILABLE
    assert result.error_code is None
    assert seen_methods == ["HEAD"]


@pytest.mark.asyncio
async def test_availability_probe_requires_upload_when_pdf_url_is_missing() -> None:
    """没有直接 PDF 地址时，候选仍可保存为需上传 PDF。"""
    probe = OpenAccessPdfAvailabilityProbe(_settings(), host_resolver=_public_resolver)
    result = await probe.probe(_candidate(fulltext_url=None))

    assert result.status is PdfAvailabilityStatus.REQUIRES_UPLOAD
    assert result.error_code is FulltextAcquisitionErrorCode.MISSING_FULLTEXT_URL


@pytest.mark.asyncio
async def test_availability_probe_rechecks_redirect_target_before_reporting_available() -> None:
    """探测也必须重新校验重定向目标，不能让私网目标伪装为可自动获取。"""

    async def resolver(host: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
        if host == "downloads.example.test":
            return (_PUBLIC_ADDRESS,)
        return (_PRIVATE_ADDRESS,)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://downloads.example.test/paper.pdf"
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/internal.pdf"})

    probe = OpenAccessPdfAvailabilityProbe(
        _settings(),
        transport=httpx.MockTransport(handler),
        host_resolver=resolver,
    )
    result = await probe.probe(_candidate())

    assert result.status is PdfAvailabilityStatus.REQUIRES_UPLOAD
    assert result.error_code is FulltextAcquisitionErrorCode.UNSAFE_URL


@pytest.mark.asyncio
async def test_authorized_uploader_accepts_a_verified_non_open_access_candidate() -> None:
    """用户上传不能依赖公开 URL，但仍必须绑定服务端已核验的候选 DOI。"""
    storage = MemoryStorage()
    result = await AuthorizedPdfUploader(_settings(), storage).acquire(
        candidate=_candidate(is_open_access=False, fulltext_url=None),
        chunks=_upload_chunks(_PDF_BYTES[:12], _PDF_BYTES[12:]),
        media_type="application/pdf",
    )

    assert result.status is FulltextAcquisitionStatus.AVAILABLE
    assert result.document is not None
    assert result.document.origin_kind == "user_upload"
    assert result.document.access_rights == "user_upload"
    assert result.document.sha256 == hashlib.sha256(_PDF_BYTES).hexdigest()
    assert storage.uploads[result.document.staging_object_key][0] == _PDF_BYTES


@pytest.mark.asyncio
async def test_authorized_uploader_rejects_a_non_pdf_request_body() -> None:
    """仅文件扩展名不能代表 PDF；上传内容必须同时通过 MIME 和魔数校验。"""
    storage = MemoryStorage()
    result = await AuthorizedPdfUploader(_settings(), storage).acquire(
        candidate=_candidate(is_open_access=False, fulltext_url=None),
        chunks=_upload_chunks(b"<html>not a pdf</html>"),
        media_type="text/html",
    )

    assert result.status is FulltextAcquisitionStatus.REJECTED
    assert result.error is not None
    assert result.error.code is FulltextAcquisitionErrorCode.INVALID_CONTENT_TYPE
    assert not storage.uploads


@pytest.mark.asyncio
async def test_acquirer_rejects_candidate_without_a_ready_doi_citation() -> None:
    """无 DOI 候选不能用上传入口绕过长期研究文献的准入规则。"""
    storage = MemoryStorage()
    acquirer = OpenAccessPdfAcquirer(_settings(), storage, host_resolver=_public_resolver)
    result = await acquirer.acquire(
        _candidate(doi=None, citation_doi=None, citation_status=CitationMetadataStatus.PARTIAL)
    )

    assert result.status is FulltextAcquisitionStatus.REJECTED
    assert result.error is not None
    assert result.error.code is FulltextAcquisitionErrorCode.CITATION_NOT_READY
    assert not storage.uploads


@pytest.mark.asyncio
async def test_acquirer_rejects_url_that_resolves_to_a_private_address() -> None:
    """每次请求前必须阻断回环或私有网络，防止候选链接形成 SSRF。"""
    storage = MemoryStorage()
    acquirer = OpenAccessPdfAcquirer(_settings(), storage, host_resolver=_private_resolver)
    result = await acquirer.acquire(_candidate())

    assert result.status is FulltextAcquisitionStatus.FAILED
    assert result.error is not None
    assert result.error.code is FulltextAcquisitionErrorCode.UNSAFE_URL
    assert not storage.uploads


@pytest.mark.asyncio
async def test_acquirer_rechecks_redirect_target_before_requesting_it() -> None:
    """安全的首跳不能授权访问后续重定向到的内网目标。"""
    storage = MemoryStorage()
    observed_hosts: list[str] = []

    async def resolver(host: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
        observed_hosts.append(host)
        return (_PRIVATE_ADDRESS,) if host == "internal.example.test" else (_PUBLIC_ADDRESS,)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "downloads.example.test"
        return httpx.Response(302, headers={"Location": "https://internal.example.test/file.pdf"})

    acquirer = OpenAccessPdfAcquirer(
        _settings(),
        storage,
        transport=httpx.MockTransport(handler),
        host_resolver=resolver,
    )
    result = await acquirer.acquire(_candidate())

    assert result.status is FulltextAcquisitionStatus.FAILED
    assert result.error is not None
    assert result.error.code is FulltextAcquisitionErrorCode.UNSAFE_URL
    assert observed_hosts == ["downloads.example.test", "internal.example.test"]
    assert not storage.uploads


@pytest.mark.asyncio
async def test_acquirer_rejects_html_response_that_claims_to_be_a_download() -> None:
    """登录页或错误页不能仅凭 200 状态被保存为 PDF。"""
    storage = MemoryStorage()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>login</html>", headers={"Content-Type": "text/html"})

    acquirer = OpenAccessPdfAcquirer(
        _settings(),
        storage,
        transport=httpx.MockTransport(handler),
        host_resolver=_public_resolver,
    )
    result = await acquirer.acquire(_candidate())

    assert result.status is FulltextAcquisitionStatus.FAILED
    assert result.error is not None
    assert result.error.code is FulltextAcquisitionErrorCode.INVALID_CONTENT_TYPE
    assert not storage.uploads


@pytest.mark.asyncio
async def test_acquirer_rejects_files_that_exceed_the_configured_size_limit() -> None:
    """下载器必须在上传前拒绝超过上限的文件。"""
    storage = MemoryStorage()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_PDF_BYTES,
            headers={"Content-Type": "application/pdf", "Content-Length": "1025"},
        )

    acquirer = OpenAccessPdfAcquirer(
        _settings(fulltext_max_file_size_bytes=1_024),
        storage,
        transport=httpx.MockTransport(handler),
        host_resolver=_public_resolver,
    )
    result = await acquirer.acquire(_candidate())

    assert result.status is FulltextAcquisitionStatus.FAILED
    assert result.error is not None
    assert result.error.code is FulltextAcquisitionErrorCode.FILE_TOO_LARGE
    assert not storage.uploads


@pytest.mark.asyncio
async def test_acquirer_reports_retryable_failure_when_staging_storage_is_unavailable() -> None:
    """PDF 校验成功但暂存失败时，结果不能伪装为可入库的全文。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_PDF_BYTES, headers={"Content-Type": "application/pdf"})

    acquirer = OpenAccessPdfAcquirer(
        _settings(),
        FailingStorage(),
        transport=httpx.MockTransport(handler),
        host_resolver=_public_resolver,
    )
    result = await acquirer.acquire(_candidate())

    assert result.status is FulltextAcquisitionStatus.FAILED
    assert result.error is not None
    assert result.error.code is FulltextAcquisitionErrorCode.STORAGE_ERROR
    assert result.error.retryable


@pytest.mark.asyncio
async def test_boto3_storage_wraps_sdk_upload_failure() -> None:
    """boto3 的专用上传异常也必须转换成稳定的存储边界异常。"""
    storage = Boto3StagingObjectStorage(_settings(), client=FailingBoto3Client())

    with pytest.raises(FulltextStorageError):
        await storage.upload_pdf(
            object_key="staging/fulltext/example.pdf",
            file=BytesIO(_PDF_BYTES),
            sha256=hashlib.sha256(_PDF_BYTES).hexdigest(),
        )


@pytest.mark.asyncio
async def test_acquirer_returns_timeout_when_the_total_acquisition_deadline_is_exceeded() -> None:
    """持续缓慢的响应也不能绕过整次全文获取的总时限。"""
    storage = MemoryStorage()

    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, content=_PDF_BYTES, headers={"Content-Type": "application/pdf"})

    acquirer = OpenAccessPdfAcquirer(
        _settings(fulltext_total_timeout_seconds=0.01),
        storage,
        transport=httpx.MockTransport(handler),
        host_resolver=_public_resolver,
    )
    result = await acquirer.acquire(_candidate())

    assert result.status is FulltextAcquisitionStatus.FAILED
    assert result.error is not None
    assert result.error.code is FulltextAcquisitionErrorCode.TIMEOUT
    assert result.error.retryable
    assert not storage.uploads


def test_settings_only_uses_the_shared_proxy_url_in_explicit_proxy_mode() -> None:
    """全文下载不继承进程全局代理，且只复用已配置的文献代理地址。"""
    direct_settings = _settings(literature_proxy_url="http://127.0.0.1:7897")
    proxy_settings = _settings(
        fulltext_network_mode="proxy",
        literature_proxy_url="http://127.0.0.1:7897",
    )

    assert direct_settings.download_proxy_url is None
    assert proxy_settings.download_proxy_url == "http://127.0.0.1:7897"
