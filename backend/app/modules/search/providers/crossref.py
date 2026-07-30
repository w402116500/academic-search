"""Crossref 文献候选获取适配器。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import httpx
from app.core.settings import CrossrefProviderSettings
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


class _HtmlTextExtractor(HTMLParser):
    """将 Crossref 偶尔返回的 JATS/HTML 摘要安全转换为纯文本。"""

    def __init__(self) -> None:
        """初始化片段列表；HTMLParser 会自动处理常见字符实体。"""
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """仅保留文本节点，忽略标签和属性以避免把标记语言展示给用户。"""
        if data.strip():
            self.parts.append(data.strip())

    def text(self) -> str | None:
        """合并文本片段；空摘要仍以 None 表示，而非空字符串。"""
        value = " ".join(self.parts).strip()
        return value or None


class CrossrefProvider:
    """将 Crossref Works API 响应转换为统一的临时候选文献。"""

    source = SourceName.CROSSREF

    # 与 OpenAlex 保持一致：这些状态应由未来编排器按策略重试，而不是立即失败整个搜索。
    _RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        settings: CrossrefProviderSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        throttle: InProcessRequestThrottle | None = None,
    ) -> None:
        """保存配置，并允许测试注入模拟网络和无等待节流器。"""
        self._settings = settings
        self._transport = transport
        self._throttle = throttle or InProcessRequestThrottle.from_requests_per_minute(
            settings.rate_limit_per_minute
        )

    async def search(self, query: ProviderQuery) -> ProviderSearchResult:
        """按偏移分页请求 Crossref Works，并映射可展示的书目信息。"""
        requested_limit = min(query.limit, self._settings.max_results)
        candidates: list[RawCandidate] = []
        offset = 0

        try:
            async with create_provider_async_client(
                base_url=self._settings.base_url,
                headers=self._build_headers(),
                timeout_seconds=self._settings.request_timeout_seconds,
                transport=self._transport,
                network=self._settings.network,
            ) as client:
                while len(candidates) < requested_limit:
                    remaining = requested_limit - len(candidates)
                    page_size = min(self._settings.page_size, remaining)
                    await self._throttle.wait_for_slot()
                    response = await client.get(
                        "/works",
                        params=self._build_params(query, offset=offset, page_size=page_size),
                    )
                    response.raise_for_status()
                    items = self._read_items(response)

                    for item in items:
                        candidate = self._to_candidate(item)

                        if candidate is not None:
                            candidates.append(candidate)

                        if len(candidates) == requested_limit:
                            break

                    # 返回数据不足一页时说明没有更多匹配项，避免发起无意义请求。
                    if len(items) < page_size:
                        break

                    offset += len(items)

            return ProviderSearchResult(
                provider=self.source,
                candidates=tuple(candidates),
                retrieved_at=datetime.now(UTC),
            )
        except httpx.TimeoutException:
            return self._failure(
                ProviderErrorCode.TIMEOUT,
                "Crossref 请求超时，请稍后重试。",
                retryable=True,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            return self._failure(
                ProviderErrorCode.REMOTE_ERROR,
                f"Crossref 返回 HTTP {status_code}。",
                retryable=status_code in self._RETRYABLE_STATUS_CODES,
                http_status_code=status_code,
            )
        except httpx.TransportError:
            return self._failure(
                ProviderErrorCode.NETWORK_ERROR,
                "无法连接 Crossref，请检查网络或代理配置。",
                retryable=True,
            )
        except ValueError:
            return self._failure(
                ProviderErrorCode.INVALID_RESPONSE,
                "Crossref 返回了无法识别的数据格式。",
                retryable=False,
            )

    def _build_headers(self) -> dict[str, str]:
        """构造 Crossref 建议的可识别 User-Agent，不包含任何敏感信息。"""
        user_agent = "academic-search/0.1.0"

        if self._settings.contact_email:
            user_agent = f"{user_agent} (mailto:{self._settings.contact_email})"

        return {
            "Accept": "application/json",
            "User-Agent": user_agent,
        }

    def _build_params(
        self,
        query: ProviderQuery,
        *,
        offset: int,
        page_size: int,
    ) -> dict[str, str | int]:
        """将统一查询转换为 Crossref 的书目信息检索与日期过滤参数。"""
        params: dict[str, str | int] = {
            "query.bibliographic": query.query,
            "rows": page_size,
            "offset": offset,
        }
        filters: list[str] = []

        if query.from_publication_year is not None:
            filters.append(f"from-pub-date:{query.from_publication_year}-01-01")

        if query.to_publication_year is not None:
            filters.append(f"until-pub-date:{query.to_publication_year}-12-31")

        if filters:
            params["filter"] = ",".join(filters)

        if self._settings.contact_email:
            params["mailto"] = self._settings.contact_email

        return params

    @staticmethod
    def _read_items(response: httpx.Response) -> Sequence[object]:
        """验证 Crossref 外层结构，并返回 Work 项目列表。"""
        payload = response.json()

        if not isinstance(payload, Mapping):
            raise ValueError("Crossref 响应不是对象")

        message = payload.get("message")

        if not isinstance(message, Mapping) or not isinstance(message.get("items"), list):
            raise ValueError("Crossref 响应缺少 message.items 数组")

        return message["items"]

    def _to_candidate(self, item: object) -> RawCandidate | None:
        """映射单个 Crossref Work；无 DOI 或标题的记录无法用于可靠候选展示。"""
        if not isinstance(item, Mapping):
            return None

        doi = self._optional_text(item.get("DOI"))
        title = self._first_text(item.get("title"))

        if doi is None or title is None:
            return None

        landing_url = self._optional_text(item.get("URL")) or f"https://doi.org/{doi}"

        return RawCandidate(
            source=self.source,
            source_record_id=doi,
            source_record_url=f"{self._settings.base_url}/works/{quote(doi, safe='')}",
            title=title,
            authors=self._authors(item.get("author")),
            abstract=self._plain_text(item.get("abstract")),
            published_year=self._published_year(item),
            published_date=self._published_date(item),
            doi=doi,
            venue=self._first_text(item.get("container-title")),
            document_type=self._optional_text(item.get("type")),
            volume=self._optional_text(item.get("volume")),
            issue=self._optional_text(item.get("issue")),
            pages=self._optional_text(item.get("page")),
            article_number=self._optional_text(item.get("article-number")),
            publisher=self._optional_text(item.get("publisher")),
            citation_count=self._optional_non_negative_integer(item.get("is-referenced-by-count")),
            landing_url=landing_url,
            fulltext_url=self._pdf_link(item.get("link")),
            # Crossref 的 link 字段不等同于开放获取授权，因此不在此处推断 OA 状态。
            is_open_access=None,
        )

    def _authors(self, value: object) -> tuple[CandidateAuthor, ...]:
        """按 Crossref 作者数组原顺序保留姓名与 ORCID（若来源提供）。"""
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return ()

        authors: list[CandidateAuthor] = []

        for author in value:
            if not isinstance(author, Mapping):
                continue

            name = self._author_name(author)

            if name is None:
                continue

            authors.append(
                CandidateAuthor(
                    name=name,
                    source_author_id=self._optional_text(author.get("ORCID")),
                )
            )

        return tuple(authors)

    def _author_name(self, author: Mapping[str, Any]) -> str | None:
        """优先组合名与姓；机构作者或非标准数据则回退到 name 字段。"""
        name = self._optional_text(author.get("name"))

        if name:
            return name

        parts = [
            value
            for value in (
                self._optional_text(author.get("given")),
                self._optional_text(author.get("family")),
            )
            if value
        ]
        combined = " ".join(parts).strip()
        return combined or None

    def _published_year(self, item: Mapping[str, Any]) -> int | None:
        """按印刷、在线、发表、签发的优先顺序提取年份，兼容不同出版元数据。"""
        publication_date = self._published_date(item)
        return publication_date.year if publication_date is not None else None

    def _published_date(self, item: Mapping[str, Any]) -> CitationDate | None:
        """保留 Crossref 给出的最完整日期，供题录引擎生成准确的 CSL 日期。"""
        for field_name in ("published-print", "published-online", "published", "issued"):
            date_value = item.get(field_name)

            if not isinstance(date_value, Mapping):
                continue

            date_parts = date_value.get("date-parts")

            if not isinstance(date_parts, Sequence) or not date_parts:
                continue

            first_date = date_parts[0]

            if not isinstance(first_date, Sequence) or not first_date:
                continue

            date = self._citation_date_from_parts(first_date)

            if date is not None:
                return date

        return None

    @staticmethod
    def _citation_date_from_parts(value: Sequence[object]) -> CitationDate | None:
        """将 Crossref ``date-parts`` 的数组转换为通过校验的内部日期模型。"""
        if len(value) > 3 or not isinstance(value[0], int) or isinstance(value[0], bool):
            return None

        optional_parts = value[1:]

        if any(not isinstance(part, int) or isinstance(part, bool) for part in optional_parts):
            return None

        month = optional_parts[0] if len(optional_parts) >= 1 else None
        day = optional_parts[1] if len(optional_parts) >= 2 else None

        if not isinstance(month, int) or isinstance(month, bool):
            month = None

        if not isinstance(day, int) or isinstance(day, bool):
            day = None

        try:
            return CitationDate(
                year=value[0],
                month=month,
                day=day,
            )
        except ValueError:
            return None

    def _pdf_link(self, value: object) -> str | None:
        """仅识别明确标注为 PDF 的链接，不把 HTML 页面伪装为全文文件。"""
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return None

        for link in value:
            if not isinstance(link, Mapping):
                continue

            content_type = self._optional_text(link.get("content-type"))

            if content_type != "application/pdf":
                continue

            pdf_url = self._optional_text(link.get("URL"))

            if pdf_url:
                return pdf_url

        return None

    @staticmethod
    def _plain_text(value: object) -> str | None:
        """清理 Crossref 摘要中的 JATS/HTML 标记，保留面向评估器的纯文本。"""
        if not isinstance(value, str) or not value.strip():
            return None

        extractor = _HtmlTextExtractor()
        extractor.feed(value)
        extractor.close()
        return extractor.text()

    @staticmethod
    def _first_text(value: object) -> str | None:
        """从 Crossref 常用的字符串数组字段中取得第一个有效文本。"""
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return None

        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()

        return None

    @staticmethod
    def _optional_text(value: object) -> str | None:
        """提取非空字符串，并统一去除首尾空白。"""
        if not isinstance(value, str):
            return None

        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _optional_non_negative_integer(value: object) -> int | None:
        """只保留可用于展示的非负整数引用计数。"""
        if not isinstance(value, int) or isinstance(value, bool):
            return None

        return value if value >= 0 else None

    def _failure(
        self,
        code: ProviderErrorCode,
        message: str,
        *,
        retryable: bool,
        http_status_code: int | None = None,
    ) -> ProviderSearchResult:
        """构造安全失败结果，让上层继续处理其余文献来源。"""
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
