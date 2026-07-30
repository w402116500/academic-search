"""arXiv 预印本文献候选获取适配器。"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from datetime import UTC, datetime

import httpx
from app.core.settings import ArxivProviderSettings
from app.modules.search.contracts import (
    CandidateAuthor,
    CitationDate,
    ProviderError,
    ProviderErrorCode,
    ProviderQuery,
    ProviderSearchResult,
    RawCandidate,
    SourceName,
)
from app.modules.search.providers.http_client import create_provider_async_client
from app.modules.search.providers.rate_limit import InProcessRequestThrottle


class ArxivProvider:
    """将 arXiv Atom API 条目转换为统一的预印本候选文献。"""

    source = SourceName.ARXIV

    # arXiv API 使用 Atom 命名空间；arXiv 自定义字段位于独立命名空间。
    _ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
    _ARXIV_NAMESPACE = "http://arxiv.org/schemas/atom"
    _NAMESPACES = {"atom": _ATOM_NAMESPACE, "arxiv": _ARXIV_NAMESPACE}
    _RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        settings: ArxivProviderSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        throttle: InProcessRequestThrottle | None = None,
    ) -> None:
        """保存配置；默认节流器遵守 arXiv 最小请求间隔建议。"""
        self._settings = settings
        self._transport = transport
        self._throttle = throttle or InProcessRequestThrottle(settings.min_request_interval_seconds)

    async def search(self, query: ProviderQuery) -> ProviderSearchResult:
        """按起始偏移分页读取 Atom Feed，返回预印本候选而不核验正式题录。"""
        requested_limit = min(query.limit, self._settings.max_results)
        candidates: list[RawCandidate] = []
        start = 0

        try:
            async with create_provider_async_client(
                headers=self._build_headers(),
                timeout_seconds=self._settings.request_timeout_seconds,
                transport=self._transport,
                network=self._settings.network,
            ) as client:
                while len(candidates) < requested_limit:
                    remaining = requested_limit - len(candidates)
                    await self._throttle.wait_for_slot()
                    response = await client.get(
                        self._settings.base_url,
                        params=self._build_params(query, start=start, max_results=remaining),
                    )
                    response.raise_for_status()
                    entries = self._read_entries(response.text)

                    for entry in entries:
                        candidate = self._to_candidate(entry)

                        if candidate is not None and self._matches_year_range(candidate, query):
                            candidates.append(candidate)

                        if len(candidates) == requested_limit:
                            break

                    # 空页或不足请求数量的一页意味着没有下一批结果。
                    if len(entries) < remaining:
                        break

                    start += len(entries)

            return ProviderSearchResult(
                provider=self.source,
                candidates=tuple(candidates),
                retrieved_at=datetime.now(UTC),
            )
        except httpx.TimeoutException:
            return self._failure(
                ProviderErrorCode.TIMEOUT,
                "arXiv 请求超时，请稍后重试。",
                retryable=True,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            return self._failure(
                ProviderErrorCode.REMOTE_ERROR,
                f"arXiv 返回 HTTP {status_code}。",
                retryable=status_code in self._RETRYABLE_STATUS_CODES,
                http_status_code=status_code,
            )
        except httpx.TransportError:
            return self._failure(
                ProviderErrorCode.NETWORK_ERROR,
                "无法连接 arXiv，请检查网络或代理配置。",
                retryable=True,
            )
        except (ElementTree.ParseError, ValueError):
            return self._failure(
                ProviderErrorCode.INVALID_RESPONSE,
                "arXiv 返回了无法识别的 Atom 数据。",
                retryable=False,
            )

    def _build_headers(self) -> dict[str, str]:
        """构造可识别的 arXiv 请求头，方便来源方区分受控客户端。"""
        user_agent = "academic-search/0.1.0"

        if self._settings.contact_email:
            user_agent = f"{user_agent} (mailto:{self._settings.contact_email})"

        return {
            "Accept": "application/atom+xml",
            "User-Agent": user_agent,
        }

    def _build_params(
        self,
        query: ProviderQuery,
        *,
        start: int,
        max_results: int,
    ) -> dict[str, str | int]:
        """构造 arXiv 的 all 字段搜索表达式与分页参数。"""
        # 移除双引号后使用短语搜索，避免用户输入破坏 arXiv 查询语法。
        safe_query = query.query.replace('"', " ").strip()

        return {
            "search_query": f'all:"{safe_query}"',
            "start": start,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

    def _read_entries(self, response_text: str) -> list[ElementTree.Element]:
        """解析 Atom XML，并仅返回顶层 entry 节点。"""
        root = ElementTree.fromstring(response_text)
        return root.findall("atom:entry", self._NAMESPACES)

    def _to_candidate(self, entry: ElementTree.Element) -> RawCandidate | None:
        """映射单个 Atom entry；缺失 arXiv ID 或标题时不生成不可追溯候选。"""
        landing_url = self._element_text(entry, "atom:id")
        title = self._element_text(entry, "atom:title")

        if landing_url is None or title is None:
            return None

        source_record_id = landing_url.rstrip("/").rsplit("/", maxsplit=1)[-1]

        if not source_record_id:
            return None

        return RawCandidate(
            source=self.source,
            source_record_id=source_record_id,
            source_record_url=landing_url,
            title=self._collapse_whitespace(title),
            authors=self._authors(entry),
            abstract=self._collapse_optional_text(self._element_text(entry, "atom:summary")),
            published_year=self._published_year(entry),
            published_date=self._published_date(entry),
            doi=self._element_text(entry, "arxiv:doi"),
            venue=self._element_text(entry, "arxiv:journal_ref"),
            document_type="preprint",
            landing_url=landing_url,
            fulltext_url=self._pdf_url(entry),
            # arXiv 条目及其 PDF 面向公众开放，但仍不能单独承担正式题录依据。
            is_open_access=True,
        )

    def _authors(self, entry: ElementTree.Element) -> tuple[CandidateAuthor, ...]:
        """按 Atom entry 中 author 节点的原始顺序保留作者姓名。"""
        authors: list[CandidateAuthor] = []

        for author in entry.findall("atom:author", self._NAMESPACES):
            name = self._element_text(author, "atom:name")

            if name:
                authors.append(CandidateAuthor(name=self._collapse_whitespace(name)))

        return tuple(authors)

    def _published_year(self, entry: ElementTree.Element) -> int | None:
        """从 ISO 8601 发布时间提取合理年份；异常日期不影响整条候选。"""
        published_date = self._published_date(entry)
        return published_date.year if published_date is not None else None

    def _published_date(self, entry: ElementTree.Element) -> CitationDate | None:
        """从 Atom 时间戳提取完整发布日期，保留年份以外的可用精度。"""
        published = self._element_text(entry, "atom:published")

        if published is None:
            return None

        try:
            parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            return None

        if not 1600 <= parsed.year <= 2100:
            return None

        return CitationDate(year=parsed.year, month=parsed.month, day=parsed.day)

    def _pdf_url(self, entry: ElementTree.Element) -> str | None:
        """找到 title=pdf 或 PDF MIME 类型的链接，避免把摘要页误标为全文。"""
        for link in entry.findall("atom:link", self._NAMESPACES):
            href = link.attrib.get("href")
            title = link.attrib.get("title")
            content_type = link.attrib.get("type")

            if href and (title == "pdf" or content_type == "application/pdf"):
                return href

        return None

    @staticmethod
    def _matches_year_range(candidate: RawCandidate, query: ProviderQuery) -> bool:
        """arXiv 未使用统一年份过滤参数时，在映射后执行同等的本地范围校验。"""
        year = candidate.published_year

        if year is None:
            return True

        if query.from_publication_year is not None and year < query.from_publication_year:
            return False

        if query.to_publication_year is not None and year > query.to_publication_year:
            return False

        return True

    def _element_text(self, element: ElementTree.Element, path: str) -> str | None:
        """提取命名空间节点的非空文本；不会将空 XML 标签转换为空字符串。"""
        child = element.find(path, self._NAMESPACES)

        if child is None or child.text is None:
            return None

        value = child.text.strip()
        return value or None

    @staticmethod
    def _collapse_whitespace(value: str) -> str:
        """将 XML 换行和连续空格压缩为普通展示文本。"""
        return " ".join(value.split())

    def _collapse_optional_text(self, value: str | None) -> str | None:
        """在可选字段中复用空白压缩逻辑。"""
        return self._collapse_whitespace(value) if value else None

    def _failure(
        self,
        code: ProviderErrorCode,
        message: str,
        *,
        retryable: bool,
        http_status_code: int | None = None,
    ) -> ProviderSearchResult:
        """构造标准失败结果，保证其他来源仍可被搜索编排器使用。"""
        return ProviderSearchResult(
            provider=self.source,
            retrieved_at=datetime.now(UTC),
            error=ProviderError(
                code=code,
                message=message,
                retryable=retryable,
                http_status_code=http_status_code,
            ),
        )
