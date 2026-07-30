"""开放获取直链 PDF 的受控下载、校验和暂存流程。"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from app.modules.fulltext.contracts import (
    AcquiredFulltext,
    FulltextAcquisitionError,
    FulltextAcquisitionErrorCode,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
)
from app.modules.fulltext.settings import FulltextAcquisitionSettings
from app.modules.fulltext.storage import FulltextStorageError, StagingObjectStorage
from app.modules.search.contracts import CitationMetadataStatus, UnifiedCandidate
from app.modules.search.normalize import normalize_doi

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
HostResolver = Callable[[str], Awaitable[tuple[IPAddress, ...]]]
_PDF_MEDIA_TYPE = "application/pdf"
_PDF_SIGNATURE = b"%PDF-"
_PDF_SIGNATURE_SEARCH_LIMIT = 1_024
_DOWNLOAD_CHUNK_SIZE = 64 * 1_024
_MEMORY_SPOOL_LIMIT = 1 * 1_024 * 1_024
_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


class _AcquisitionFailure(Exception):
    """在内部提前终止下载，并携带可安全返回给调用方的失败信息。"""

    def __init__(
        self,
        *,
        status: FulltextAcquisitionStatus,
        code: FulltextAcquisitionErrorCode,
        message: str,
        retryable: bool,
        http_status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error = FulltextAcquisitionError(
            code=code,
            message=message,
            retryable=retryable,
            http_status_code=http_status_code,
        )


class OpenAccessPdfAcquirer:
    """只获取已核验候选中明确标注为开放获取的直接 PDF。"""

    def __init__(
        self,
        settings: FulltextAcquisitionSettings,
        storage: StagingObjectStorage,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        host_resolver: HostResolver | None = None,
    ) -> None:
        """注入配置、存储与可替换网络边界，使安全逻辑能离线测试。"""
        self._settings = settings
        self._storage = storage
        self._transport = transport
        self._host_resolver = host_resolver or resolve_public_host

    async def acquire(self, candidate: UnifiedCandidate) -> FulltextAcquisitionResult:
        """在总时限内下载并暂存候选 PDF，绝不接收调用方提供的任意 URL。"""
        try:
            return await asyncio.wait_for(
                self._acquire(candidate),
                timeout=self._settings.fulltext_total_timeout_seconds,
            )
        except TimeoutError:
            return self._failure(
                candidate,
                FulltextAcquisitionErrorCode.TIMEOUT,
                "全文获取超过总时限，请稍后重试。",
                retryable=True,
            )

    async def _acquire(self, candidate: UnifiedCandidate) -> FulltextAcquisitionResult:
        """执行可取消的候选准入、下载校验和对象暂存流程。"""
        try:
            doi = self._validate_candidate(candidate)
            source_url = await self._validate_download_url(candidate.links.fulltext_url)
            document = await self._download_and_store(candidate, doi=doi, source_url=source_url)
            return FulltextAcquisitionResult(
                candidate_id=candidate.candidate_id,
                status=FulltextAcquisitionStatus.AVAILABLE,
                document=document,
            )
        except _AcquisitionFailure as exc:
            return FulltextAcquisitionResult(
                candidate_id=candidate.candidate_id,
                status=exc.status,
                error=exc.error,
            )
        except httpx.TimeoutException:
            return self._failure(
                candidate,
                FulltextAcquisitionErrorCode.TIMEOUT,
                "全文下载超时，请稍后重试。",
                retryable=True,
            )
        except httpx.TransportError:
            return self._failure(
                candidate,
                FulltextAcquisitionErrorCode.NETWORK_ERROR,
                "无法连接全文来源，请检查网络后重试。",
                retryable=True,
            )
        except FulltextStorageError:
            return self._failure(
                candidate,
                FulltextAcquisitionErrorCode.STORAGE_ERROR,
                "全文已校验，但暂时无法写入私有对象存储。",
                retryable=True,
            )

    def _validate_candidate(self, candidate: UnifiedCandidate) -> str:
        """在发起网络请求前落实 DOI、题录和开放获取三项准入条件。"""
        citation = candidate.citation

        if citation is None or citation.status is not CitationMetadataStatus.READY:
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.REJECTED,
                code=FulltextAcquisitionErrorCode.CITATION_NOT_READY,
                message="题录尚未完成 DOI 核验，不能获取全文。",
                retryable=False,
            )

        doi = normalize_doi(candidate.doi)
        citation_doi = normalize_doi(citation.doi)

        if doi is None or citation_doi is None:
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.REJECTED,
                code=FulltextAcquisitionErrorCode.MISSING_DOI,
                message="候选缺少已核验 DOI，不能进入文献研究库。",
                retryable=False,
            )

        if doi != citation_doi:
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.REJECTED,
                code=FulltextAcquisitionErrorCode.DOI_MISMATCH,
                message="候选 DOI 与核验题录不一致，不能自动获取全文。",
                retryable=False,
            )

        if candidate.is_open_access is not True:
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.REQUIRES_UPLOAD,
                code=FulltextAcquisitionErrorCode.NOT_OPEN_ACCESS,
                message="该候选未被来源明确标记为开放获取，请上传有权处理的 PDF。",
                retryable=False,
            )

        if not candidate.links.fulltext_url:
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.REQUIRES_UPLOAD,
                code=FulltextAcquisitionErrorCode.MISSING_FULLTEXT_URL,
                message="该开放获取候选未提供直接 PDF 地址，请上传有权处理的 PDF。",
                retryable=False,
            )

        return doi

    async def _validate_download_url(self, url: str | None) -> str:
        """限制 URL 协议、端口和公网解析结果，阻止下载器成为 SSRF 通道。"""
        if url is None:
            raise AssertionError("候选准入检查后全文 URL 不应为空")

        parsed = urlsplit(url)

        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.hostname is None
        ):
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.FAILED,
                code=FulltextAcquisitionErrorCode.INVALID_URL,
                message="全文地址不是可安全访问的 HTTPS 直链。",
                retryable=False,
            )

        try:
            port = parsed.port
        except ValueError as exc:
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.FAILED,
                code=FulltextAcquisitionErrorCode.INVALID_URL,
                message="全文地址包含无效端口。",
                retryable=False,
            ) from exc

        if port not in {None, 443}:
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.FAILED,
                code=FulltextAcquisitionErrorCode.UNSAFE_URL,
                message="全文地址使用了不允许的 HTTPS 端口。",
                retryable=False,
            )

        await self._require_public_host(parsed.hostname)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))

    async def _require_public_host(self, host: str) -> None:
        """解析主机并拒绝任意私有、回环、保留或无法解析的地址。"""
        try:
            addresses = await self._host_resolver(host)
        except OSError as exc:
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.FAILED,
                code=FulltextAcquisitionErrorCode.NETWORK_ERROR,
                message="无法解析全文来源地址。",
                retryable=True,
            ) from exc

        if not addresses or any(not address.is_global for address in addresses):
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.FAILED,
                code=FulltextAcquisitionErrorCode.UNSAFE_URL,
                message="全文地址解析到了不允许访问的网络位置。",
                retryable=False,
            )

    async def _download_and_store(
        self,
        candidate: UnifiedCandidate,
        *,
        doi: str,
        source_url: str,
    ) -> AcquiredFulltext:
        """逐跳跟随已校验重定向，校验 PDF 后写入私有暂存对象。"""
        current_url = source_url
        redirects = 0

        async with httpx.AsyncClient(
            headers={"Accept": _PDF_MEDIA_TYPE, "User-Agent": "academic-search/0.1.0"},
            timeout=httpx.Timeout(self._settings.fulltext_download_timeout_seconds),
            follow_redirects=False,
            transport=self._transport,
            proxy=self._settings.download_proxy_url,
            trust_env=False,
        ) as client:
            while True:
                async with client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        current_url = await self._next_redirect_url(
                            current_url,
                            response.headers.get("Location"),
                            redirects=redirects,
                        )
                        redirects += 1
                        continue

                    if response.status_code != 200:
                        raise _AcquisitionFailure(
                            status=FulltextAcquisitionStatus.FAILED,
                            code=FulltextAcquisitionErrorCode.REMOTE_ERROR,
                            message=f"全文来源返回 HTTP {response.status_code}。",
                            retryable=response.status_code in _RETRYABLE_HTTP_STATUS_CODES,
                            http_status_code=response.status_code,
                        )

                    self._validate_response_headers(response)
                    return await self._stream_validate_and_store(
                        candidate,
                        doi=doi,
                        source_url=current_url,
                        response=response,
                    )

    async def _next_redirect_url(
        self,
        current_url: str,
        location: str | None,
        *,
        redirects: int,
    ) -> str:
        """每个跳转都重新校验，不能让首个安全 URL 带入内网地址。"""
        if redirects >= self._settings.fulltext_max_redirects:
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.FAILED,
                code=FulltextAcquisitionErrorCode.REDIRECT_LIMIT_EXCEEDED,
                message="全文下载的重定向次数超过限制。",
                retryable=False,
            )

        if not location:
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.FAILED,
                code=FulltextAcquisitionErrorCode.INVALID_URL,
                message="全文来源返回了缺少目标地址的重定向。",
                retryable=False,
            )

        return await self._validate_download_url(urljoin(current_url, location))

    def _validate_response_headers(self, response: httpx.Response) -> None:
        """在读取正文前用声明大小和 MIME 类型快速拒绝明显不合规响应。"""
        content_type = (
            response.headers.get("Content-Type", "").split(";", maxsplit=1)[0].strip().lower()
        )

        if content_type != _PDF_MEDIA_TYPE:
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.FAILED,
                code=FulltextAcquisitionErrorCode.INVALID_CONTENT_TYPE,
                message="全文来源未返回 application/pdf 文件。",
                retryable=False,
                http_status_code=response.status_code,
            )

        content_length = response.headers.get("Content-Length")

        if content_length is None:
            return

        try:
            declared_size = int(content_length)
        except ValueError:
            return

        if declared_size > self._settings.fulltext_max_file_size_bytes:
            raise _AcquisitionFailure(
                status=FulltextAcquisitionStatus.FAILED,
                code=FulltextAcquisitionErrorCode.FILE_TOO_LARGE,
                message="全文文件超过允许的最大大小。",
                retryable=False,
                http_status_code=response.status_code,
            )

    async def _stream_validate_and_store(
        self,
        candidate: UnifiedCandidate,
        *,
        doi: str,
        source_url: str,
        response: httpx.Response,
    ) -> AcquiredFulltext:
        """流式读取文件、限制体积、检查 PDF 签名并上传暂存对象。"""
        sha256 = hashlib.sha256()
        byte_size = 0
        signature_prefix = bytearray()

        with SpooledTemporaryFile(max_size=_MEMORY_SPOOL_LIMIT, mode="w+b") as temporary_file:
            async for chunk in response.aiter_bytes(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                byte_size += len(chunk)

                if byte_size > self._settings.fulltext_max_file_size_bytes:
                    raise _AcquisitionFailure(
                        status=FulltextAcquisitionStatus.FAILED,
                        code=FulltextAcquisitionErrorCode.FILE_TOO_LARGE,
                        message="全文文件超过允许的最大大小。",
                        retryable=False,
                        http_status_code=response.status_code,
                    )

                if len(signature_prefix) < _PDF_SIGNATURE_SEARCH_LIMIT:
                    remaining = _PDF_SIGNATURE_SEARCH_LIMIT - len(signature_prefix)
                    signature_prefix.extend(chunk[:remaining])

                sha256.update(chunk)
                temporary_file.write(chunk)

            if not byte_size or _PDF_SIGNATURE not in signature_prefix:
                raise _AcquisitionFailure(
                    status=FulltextAcquisitionStatus.FAILED,
                    code=FulltextAcquisitionErrorCode.INVALID_PDF,
                    message="全文内容未通过 PDF 文件签名校验。",
                    retryable=False,
                    http_status_code=response.status_code,
                )

            digest = sha256.hexdigest()
            object_key = self._staging_object_key(candidate.candidate_id, digest)
            await self._storage.upload_pdf(
                object_key=object_key,
                file=cast(BinaryIO, temporary_file),
                sha256=digest,
            )

        return AcquiredFulltext(
            candidate_id=candidate.candidate_id,
            doi=doi,
            source_url=source_url,
            staging_object_key=object_key,
            original_filename="fulltext.pdf",
            byte_size=byte_size,
            sha256=digest,
            acquired_at=datetime.now(UTC),
        )

    def _staging_object_key(self, candidate_id: object, sha256: str) -> str:
        """以候选 ID 和内容哈希生成不可猜测路径外的稳定暂存对象键。"""
        return f"{self._settings.fulltext_staging_prefix}/{candidate_id}/{sha256}.pdf"

    @staticmethod
    def _failure(
        candidate: UnifiedCandidate,
        code: FulltextAcquisitionErrorCode,
        message: str,
        *,
        retryable: bool,
    ) -> FulltextAcquisitionResult:
        """统一构造网络和存储边界的运行时失败结果。"""
        return FulltextAcquisitionResult(
            candidate_id=candidate.candidate_id,
            status=FulltextAcquisitionStatus.FAILED,
            error=FulltextAcquisitionError(code=code, message=message, retryable=retryable),
        )


async def resolve_public_host(host: str) -> tuple[IPAddress, ...]:
    """解析域名并返回去重地址，供每次初始请求和跳转前的 SSRF 预检查使用。"""
    try:
        direct_address = ipaddress.ip_address(host)
    except ValueError:
        direct_address = None

    if direct_address is not None:
        return (direct_address,)

    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses: set[IPAddress] = set()

    for _, _, _, _, sockaddr in records:
        addresses.add(ipaddress.ip_address(sockaddr[0]))

    return tuple(sorted(addresses, key=str))
