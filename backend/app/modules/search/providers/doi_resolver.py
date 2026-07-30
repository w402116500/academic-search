"""通过 DOI Content Negotiation 取得 CSL-JSON 题录。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import quote

import httpx
from app.core.settings import DoiResolverSettings
from app.modules.search.contracts import (
    CitationAuthor,
    CitationDate,
    CitationResolutionError,
    CitationResolutionErrorCode,
    DoiCslRecord,
    DoiMetadataResolution,
)
from app.modules.search.normalize import normalize_doi
from app.modules.search.providers.http_client import create_provider_async_client


class DoiMetadataResolver:
    """向 DOI 注册表请求格式中立的 CSL-JSON 元数据。

    本类只处理一次外部请求与响应解析，不判断候选是否可引用，也不与搜索候选
    合并。这样网络边界保持独立，合并规则可用纯函数离线测试。
    """

    _RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
    _CSL_JSON_ACCEPT = "application/vnd.citationstyles.csl+json"

    def __init__(
        self,
        settings: DoiResolverSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """保存已校验的配置，允许测试注入 MockTransport 而不访问真实 DOI 服务。"""
        self._settings = settings
        self._transport = transport

    async def resolve(self, doi: str) -> DoiMetadataResolution:
        """解析单个 DOI；任何远端问题都转换为可展示的明确失败结果。"""
        normalized_doi = normalize_doi(doi)

        if normalized_doi is None:
            return self._failure(
                doi=doi,
                code=CitationResolutionErrorCode.INVALID_RESPONSE,
                message="DOI 格式无效，无法请求正式题录。",
                retryable=False,
            )

        try:
            async with create_provider_async_client(
                base_url=self._settings.base_url,
                headers={
                    "Accept": self._CSL_JSON_ACCEPT,
                    "User-Agent": "academic-search/0.1.0",
                },
                timeout_seconds=self._settings.request_timeout_seconds,
                transport=self._transport,
                network=self._settings.network,
                # DOI 的内容协商可能经 30x 跳转到元数据提供方，需显式允许跟随。
                follow_redirects=True,
            ) as client:
                response = await client.get(f"/{quote(normalized_doi, safe='/')}")
                response.raise_for_status()
                record = self._to_record(response, normalized_doi)
        except httpx.TimeoutException:
            return self._failure(
                doi=normalized_doi,
                code=CitationResolutionErrorCode.TIMEOUT,
                message="DOI 内容协商请求超时，请稍后重试。",
                retryable=True,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            return self._failure(
                doi=normalized_doi,
                code=CitationResolutionErrorCode.REMOTE_ERROR,
                message=f"DOI 内容协商返回 HTTP {status_code}。",
                retryable=status_code in self._RETRYABLE_STATUS_CODES,
                http_status_code=status_code,
            )
        except httpx.TransportError:
            return self._failure(
                doi=normalized_doi,
                code=CitationResolutionErrorCode.NETWORK_ERROR,
                message="无法连接 DOI 内容协商服务，请检查网络或代理配置。",
                retryable=True,
            )
        except ValueError:
            return self._failure(
                doi=normalized_doi,
                code=CitationResolutionErrorCode.INVALID_RESPONSE,
                message="DOI 内容协商返回了无法识别的 CSL-JSON 题录。",
                retryable=False,
            )

        return DoiMetadataResolution(doi=normalized_doi, record=record)

    def _to_record(self, response: httpx.Response, requested_doi: str) -> DoiCslRecord:
        """验证 CSL-JSON 最小结构，并提取当前项目支持的题录字段。"""
        payload = response.json()

        if not isinstance(payload, Mapping):
            raise ValueError("CSL-JSON 根节点必须是对象")

        title = self._optional_text(payload.get("title"))

        if title is None:
            raise ValueError("CSL-JSON 缺少 title")

        return DoiCslRecord(
            source_url=f"{self._settings.base_url}/{quote(requested_doi, safe='/')}",
            doi=normalize_doi(self._optional_text(payload.get("DOI"))) or requested_doi,
            authors=self._authors(payload.get("author")),
            title=title,
            document_type=self._optional_text(payload.get("type")),
            issued_date=self._citation_date(payload.get("issued")),
            venue=self._optional_text(payload.get("container-title")),
            volume=self._optional_text(payload.get("volume")),
            issue=self._optional_text(payload.get("issue")),
            pages=self._optional_text(payload.get("page")),
            article_number=self._optional_text(payload.get("article-number")),
            publisher=self._optional_text(payload.get("publisher")),
            url=self._optional_text(payload.get("URL")),
        )

    def _authors(self, value: object) -> tuple[CitationAuthor, ...]:
        """仅保留 CSL 规范的个人或机构作者，异常单项不影响整条题录补全。"""
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return ()

        authors: list[CitationAuthor] = []

        for author in value:
            if not isinstance(author, Mapping):
                continue

            literal = self._optional_text(author.get("literal"))

            if literal is not None:
                authors.append(CitationAuthor(literal=literal))
                continue

            family = self._optional_text(author.get("family"))

            if family is None:
                continue

            authors.append(
                CitationAuthor(
                    family=family,
                    given=self._optional_text(author.get("given")),
                )
            )

        return tuple(authors)

    @staticmethod
    def _citation_date(value: object) -> CitationDate | None:
        """解析标准 CSL ``issued.date-parts``，缺失日期不会使整条元数据失效。"""
        if not isinstance(value, Mapping):
            return None

        date_parts = value.get("date-parts")

        if not isinstance(date_parts, Sequence) or not date_parts:
            return None

        first_date = date_parts[0]

        if not isinstance(first_date, Sequence) or isinstance(first_date, (str, bytes)):
            return None

        if not 1 <= len(first_date) <= 3:
            return None

        if any(not isinstance(part, int) or isinstance(part, bool) for part in first_date):
            return None

        try:
            return CitationDate(
                year=first_date[0],
                month=first_date[1] if len(first_date) >= 2 else None,
                day=first_date[2] if len(first_date) >= 3 else None,
            )
        except ValueError:
            return None

    @staticmethod
    def _optional_text(value: object) -> str | None:
        """接受 CSL 字符串字段并剔除空白；其他类型不做宽松转换。"""
        if not isinstance(value, str):
            return None

        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _failure(
        *,
        doi: str,
        code: CitationResolutionErrorCode,
        message: str,
        retryable: bool,
        http_status_code: int | None = None,
    ) -> DoiMetadataResolution:
        """构造不包含第三方响应正文的失败结果，供调用方明确提示与重试。"""
        return DoiMetadataResolution(
            doi=doi,
            error=CitationResolutionError(
                code=code,
                message=message,
                retryable=retryable,
                http_status_code=http_status_code,
            ),
        )
