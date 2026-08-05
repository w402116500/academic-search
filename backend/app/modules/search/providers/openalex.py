"""OpenAlex 文献候选获取适配器。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.settings import OpenAlexProviderSettings
from app.modules.literature.contracts import CitationDate
from app.modules.search.contracts import (
    CandidateAuthor,
    ProviderError,
    ProviderErrorCode,
    ProviderQuery,
    ProviderSearchResult,
    RawCandidate,
    SourceName,
)
from app.modules.search.normalize import normalize_candidate_language
from app.modules.search.providers.http_client import create_provider_async_client
from app.modules.search.providers.rate_limit import InProcessRequestThrottle


class OpenAlexProvider:
    """将 OpenAlex Works API 响应转换为项目统一的临时候选文献。"""

    source = SourceName.OPENALEX

    # 这些 HTTP 状态通常值得由未来的编排器按退避策略重试一次或多次。
    _RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        settings: OpenAlexProviderSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        throttle: InProcessRequestThrottle | None = None,
    ) -> None:
        """保存来源配置；可注入 transport 与节流器以支持确定性离线测试。"""
        self._settings = settings
        self._transport = transport
        self._throttle = throttle or InProcessRequestThrottle.from_requests_per_minute(
            settings.rate_limit_per_minute
        )

    async def search(self, query: ProviderQuery) -> ProviderSearchResult:
        """根据查询计划分页获取 OpenAlex Works，并安全转换为统一候选结构。"""
        requested_limit = min(query.limit, self._settings.max_results)
        candidates: list[RawCandidate] = []
        cursor: str | None = "*"

        try:
            async with create_provider_async_client(
                base_url=self._settings.base_url,
                headers=self._build_headers(),
                timeout_seconds=self._settings.request_timeout_seconds,
                transport=self._transport,
                network=self._settings.network,
            ) as client:
                while cursor is not None and len(candidates) < requested_limit:
                    remaining = requested_limit - len(candidates)
                    page_size = min(self._settings.page_size, remaining)
                    await self._throttle.wait_for_slot()
                    response = await client.get(
                        "/works",
                        params=self._build_params(query, cursor=cursor, page_size=page_size),
                    )
                    response.raise_for_status()
                    payload = self._read_payload(response)

                    # OpenAlex 可能返回缺字段记录；单条异常不应使整批检索失败。
                    for work in payload["results"]:
                        candidate = self._to_candidate(work)

                        if candidate is not None:
                            candidates.append(candidate)

                        if len(candidates) == requested_limit:
                            break

                    cursor = self._next_cursor(payload)

            return ProviderSearchResult(
                provider=self.source,
                candidates=tuple(candidates),
                retrieved_at=datetime.now(UTC),
            )
        except httpx.TimeoutException:
            return self._failure(
                ProviderErrorCode.TIMEOUT,
                "OpenAlex 请求超时，请稍后重试。",
                retryable=True,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            return self._failure(
                ProviderErrorCode.REMOTE_ERROR,
                f"OpenAlex 返回 HTTP {status_code}。",
                retryable=status_code in self._RETRYABLE_STATUS_CODES,
                http_status_code=status_code,
            )
        except httpx.TransportError:
            return self._failure(
                ProviderErrorCode.NETWORK_ERROR,
                "无法连接 OpenAlex，请检查网络或代理配置。",
                retryable=True,
            )
        except ValueError:
            # 响应解析错误不附带原始正文，避免未知内容进入日志或 SSE 事件。
            return self._failure(
                ProviderErrorCode.INVALID_RESPONSE,
                "OpenAlex 返回了无法识别的数据格式。",
                retryable=False,
            )

    def _build_headers(self) -> dict[str, str]:
        """构造不含密钥的公共请求头，密钥只会放入 HTTPS 查询参数。"""
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
        cursor: str,
        page_size: int,
    ) -> dict[str, str | int]:
        """将统一查询转换为 OpenAlex Works API 的受支持参数。"""
        params: dict[str, str | int] = {
            "search": query.query,
            "per-page": page_size,
            "cursor": cursor,
        }
        filters: list[str] = []

        if query.from_publication_year is not None:
            filters.append(f"from_publication_date:{query.from_publication_year}-01-01")

        if query.to_publication_year is not None:
            filters.append(f"to_publication_date:{query.to_publication_year}-12-31")

        if filters:
            params["filter"] = ",".join(filters)

        if self._settings.contact_email:
            params["mailto"] = self._settings.contact_email

        if self._settings.api_key:
            # 不记录 params，防止 API Key 经调试日志、异常或 SSE 泄露到客户端。
            params["api_key"] = self._settings.api_key.get_secret_value()

        return params

    @staticmethod
    def _read_payload(response: httpx.Response) -> Mapping[str, Any]:
        """读取并验证最小响应骨架，拒绝不符合 Works API 预期的 JSON。"""
        payload = response.json()

        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("OpenAlex 响应缺少 results 数组")

        return payload

    @staticmethod
    def _next_cursor(payload: Mapping[str, Any]) -> str | None:
        """取出下一页游标；来源未提供游标时自然结束分页。"""
        metadata = payload.get("meta")

        if not isinstance(metadata, Mapping):
            return None

        cursor = metadata.get("next_cursor")
        return cursor.strip() if isinstance(cursor, str) and cursor.strip() else None

    def _to_candidate(self, work: object) -> RawCandidate | None:
        """映射单条 Works 记录；缺少稳定 ID 或标题的记录不具备展示价值。"""
        if not isinstance(work, Mapping):
            return None

        source_record_url = self._optional_text(work.get("id"))
        title = self._optional_text(work.get("display_name"))

        if source_record_url is None or title is None:
            return None

        source_record_id = source_record_url.rstrip("/").rsplit("/", maxsplit=1)[-1]

        if not source_record_id:
            return None

        primary_location = self._mapping(work.get("primary_location"))
        best_oa_location = self._mapping(work.get("best_oa_location"))
        open_access = self._mapping(work.get("open_access"))
        biblio = self._mapping(work.get("biblio"))
        venue = self._venue_name(primary_location)
        landing_url = self._optional_text(primary_location.get("landing_page_url"))
        open_access_url = self._optional_text(open_access.get("oa_url"))
        fulltext_url = self._optional_text(best_oa_location.get("pdf_url"))

        return RawCandidate(
            source=self.source,
            source_record_id=source_record_id,
            source_record_url=source_record_url,
            title=title,
            language=normalize_candidate_language(work.get("language")),
            authors=self._authors(work.get("authorships")),
            abstract=self._restore_abstract(work.get("abstract_inverted_index")),
            published_year=self._optional_year(work.get("publication_year")),
            published_date=self._optional_citation_date(work.get("publication_date")),
            doi=self._optional_text(work.get("doi")),
            venue=venue,
            document_type=self._optional_text(work.get("type")),
            volume=self._optional_text(biblio.get("volume")),
            issue=self._optional_text(biblio.get("issue")),
            pages=self._page_range(biblio),
            citation_count=self._optional_non_negative_integer(work.get("cited_by_count")),
            landing_url=landing_url or open_access_url or source_record_url,
            open_access_url=open_access_url,
            fulltext_url=fulltext_url,
            is_open_access=self._optional_boolean(open_access.get("is_oa")),
        )

    @staticmethod
    def _mapping(value: object) -> Mapping[str, Any]:
        """将缺失或异常的嵌套对象视为安全空映射，简化来源字段缺失处理。"""
        return value if isinstance(value, Mapping) else {}

    def _venue_name(self, primary_location: Mapping[str, Any]) -> str | None:
        """从主发布位置提取期刊或会议名称；来源缺失时保留为空。"""
        source = self._mapping(primary_location.get("source"))
        return self._optional_text(source.get("display_name"))

    def _authors(self, value: object) -> tuple[CandidateAuthor, ...]:
        """按 OpenAlex authorships 原顺序保留可用作者，忽略缺失姓名的异常项。"""
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return ()

        authors: list[CandidateAuthor] = []

        for authorship in value:
            if not isinstance(authorship, Mapping):
                continue

            author = self._mapping(authorship.get("author"))
            author_name = self._optional_text(author.get("display_name"))

            if author_name is None:
                continue

            authors.append(
                CandidateAuthor(
                    name=author_name,
                    source_author_id=self._optional_text(author.get("id")),
                )
            )

        return tuple(authors)

    @staticmethod
    def _restore_abstract(value: object) -> str | None:
        """把 OpenAlex 的词语-位置倒排索引还原为按原文顺序排列的摘要。"""
        if not isinstance(value, Mapping):
            return None

        positioned_tokens: list[tuple[int, str]] = []

        for token, positions in value.items():
            if not isinstance(token, str) or not isinstance(positions, Sequence):
                continue

            for position in positions:
                # bool 是 int 的子类，必须排除，避免 True 被误解释为第 1 个词。
                if isinstance(position, int) and not isinstance(position, bool) and position >= 0:
                    positioned_tokens.append((position, token))

        if not positioned_tokens:
            return None

        positioned_tokens.sort(key=lambda item: item[0])
        return " ".join(token for _, token in positioned_tokens)

    @staticmethod
    def _optional_text(value: object) -> str | None:
        """提取非空文本，并在写入候选前去除首尾空白。"""
        if not isinstance(value, str):
            return None

        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _optional_year(value: object) -> int | None:
        """接受 OpenAlex 的整数年份，拒绝 bool 与不合理年份。"""
        if not isinstance(value, int) or isinstance(value, bool):
            return None

        return value if 1600 <= value <= 2100 else None

    @staticmethod
    def _optional_citation_date(value: object) -> CitationDate | None:
        """解析 OpenAlex 的 ISO 日期；只给出年份时仍保留可用于 CSL 的年份。"""
        if not isinstance(value, str) or not value.strip():
            return None

        date_parts = value.strip().split("-")

        try:
            year = int(date_parts[0])
            month = int(date_parts[1]) if len(date_parts) >= 2 else None
            day = int(date_parts[2]) if len(date_parts) >= 3 else None
            return CitationDate(year=year, month=month, day=day)
        except (TypeError, ValueError):
            return None

    def _page_range(self, biblio: Mapping[str, Any]) -> str | None:
        """将 OpenAlex 分离的起止页码合成为书目展示使用的页码范围。"""
        first_page = self._optional_text(biblio.get("first_page"))
        last_page = self._optional_text(biblio.get("last_page"))

        if first_page is None:
            return None

        if last_page is None or last_page == first_page:
            return first_page

        return f"{first_page}-{last_page}"

    @staticmethod
    def _optional_non_negative_integer(value: object) -> int | None:
        """仅保留非负整数引用量，防止来源异常值破坏后续排序。"""
        if not isinstance(value, int) or isinstance(value, bool):
            return None

        return value if value >= 0 else None

    @staticmethod
    def _optional_boolean(value: object) -> bool | None:
        """只接受 JSON 布尔值，避免字符串 true/false 被宽松转换。"""
        return value if isinstance(value, bool) else None

    def _failure(
        self,
        code: ProviderErrorCode,
        message: str,
        *,
        retryable: bool,
        http_status_code: int | None = None,
    ) -> ProviderSearchResult:
        """构造标准失败结果，使上层可以继续等待其他来源完成。"""
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
