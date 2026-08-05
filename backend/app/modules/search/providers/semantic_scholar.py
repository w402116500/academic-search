"""Semantic Scholar 文献候选获取适配器。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from app.core.settings import SemanticScholarProviderSettings
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
from app.modules.search.providers.http_client import create_provider_async_client
from app.modules.search.providers.rate_limit import InProcessRequestThrottle


class SemanticScholarProvider:
    """将 Semantic Scholar Graph API 搜索结果转换为统一候选文献。"""

    source = SourceName.SEMANTIC_SCHOLAR

    # 只请求当前候选展示需要的字段，避免传输完整引用图造成延迟和不必要成本。
    _FIELDS = (
        "paperId,title,abstract,authors,year,externalIds,venue,"
        "citationCount,openAccessPdf,url,publicationTypes,journal,"
        "publicationDate,publicationVenue"
    )
    _RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        settings: SemanticScholarProviderSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        throttle: InProcessRequestThrottle | None = None,
    ) -> None:
        """保存配置；API Key 仅保存在请求头，不会出现在 URL 或错误对象中。"""
        self._settings = settings
        self._transport = transport
        self._throttle = throttle or InProcessRequestThrottle.from_requests_per_minute(
            settings.rate_limit_per_minute
        )

    async def search(self, query: ProviderQuery) -> ProviderSearchResult:
        """按 offset 分页调用 Paper Search，并保留来源给出的引用量与开放 PDF。"""
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
                    page_size = min(remaining, 100)
                    await self._throttle.wait_for_slot()
                    response = await client.get(
                        "paper/search",
                        params=self._build_params(query, offset=offset, limit=page_size),
                    )
                    response.raise_for_status()
                    papers = self._read_papers(response)

                    for paper in papers:
                        candidate = self._to_candidate(paper)

                        if candidate is not None:
                            candidates.append(candidate)

                        if len(candidates) == requested_limit:
                            break

                    if len(papers) < page_size:
                        break

                    offset += len(papers)

            return ProviderSearchResult(
                provider=self.source,
                candidates=tuple(candidates),
                retrieved_at=datetime.now(UTC),
            )
        except httpx.TimeoutException:
            return self._failure(
                ProviderErrorCode.TIMEOUT,
                "Semantic Scholar 请求超时，请稍后重试。",
                retryable=True,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            return self._failure(
                ProviderErrorCode.REMOTE_ERROR,
                f"Semantic Scholar 返回 HTTP {status_code}。",
                retryable=status_code in self._RETRYABLE_STATUS_CODES,
                http_status_code=status_code,
            )
        except httpx.TransportError:
            return self._failure(
                ProviderErrorCode.NETWORK_ERROR,
                "无法连接 Semantic Scholar，请检查网络或代理配置。",
                retryable=True,
            )
        except ValueError:
            return self._failure(
                ProviderErrorCode.INVALID_RESPONSE,
                "Semantic Scholar 返回了无法识别的数据格式。",
                retryable=False,
            )

    def _build_headers(self) -> dict[str, str]:
        """按已解析的访问通道构造官方 API Key 或 Ominiai Bearer 鉴权头。"""
        headers = {"Accept": "application/json"}

        if self._settings.auth_token is None:
            return headers

        token = self._settings.auth_token.get_secret_value()

        if self._settings.auth_mode == "x_api_key":
            headers["x-api-key"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"

        return headers

    def _build_params(
        self,
        query: ProviderQuery,
        *,
        offset: int,
        limit: int,
    ) -> dict[str, str | int]:
        """将统一查询和年份范围映射为 Semantic Scholar Paper Search 参数。"""
        params: dict[str, str | int] = {
            "query": query.query,
            "limit": limit,
            "offset": offset,
            "fields": self._FIELDS,
        }

        if query.from_publication_year is not None and query.to_publication_year is not None:
            params["year"] = f"{query.from_publication_year}-{query.to_publication_year}"
        elif query.from_publication_year is not None:
            params["year"] = f"{query.from_publication_year}-"
        elif query.to_publication_year is not None:
            params["year"] = f"-{query.to_publication_year}"

        return params

    @staticmethod
    def _read_papers(response: httpx.Response) -> Sequence[object]:
        """验证 Graph API 的 data 数组结构。"""
        payload = response.json()

        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
            raise ValueError("Semantic Scholar 响应缺少 data 数组")

        return payload["data"]

    def _to_candidate(self, paper: object) -> RawCandidate | None:
        """映射单篇论文；缺少 paperId 或标题时不返回不可追溯候选。"""
        if not isinstance(paper, Mapping):
            return None

        paper_id = self._optional_text(paper.get("paperId"))
        title = self._optional_text(paper.get("title"))

        if paper_id is None or title is None:
            return None

        external_ids = self._mapping(paper.get("externalIds"))
        open_access_pdf = self._mapping(paper.get("openAccessPdf"))
        journal = self._mapping(paper.get("journal"))
        publication_venue = self._mapping(paper.get("publicationVenue"))
        pdf_url = self._optional_text(open_access_pdf.get("url"))
        publication_types = paper.get("publicationTypes")

        return RawCandidate(
            source=self.source,
            source_record_id=paper_id,
            source_record_url=f"{self._settings.base_url}/paper/{quote(paper_id, safe='')}",
            title=title,
            authors=self._authors(paper.get("authors")),
            abstract=self._optional_text(paper.get("abstract")),
            published_year=self._optional_year(paper.get("year")),
            published_date=self._optional_citation_date(paper.get("publicationDate")),
            doi=self._optional_text(external_ids.get("DOI")),
            venue=self._venue_name(paper.get("venue"), journal, publication_venue),
            document_type=self._first_text(publication_types),
            volume=self._optional_text(journal.get("volume")),
            issue=self._optional_text(journal.get("issue")),
            pages=self._optional_text(journal.get("pages")),
            citation_count=self._optional_non_negative_integer(paper.get("citationCount")),
            landing_url=self._optional_text(paper.get("url")),
            open_access_url=pdf_url,
            fulltext_url=pdf_url,
            is_open_access=pdf_url is not None,
        )

    @staticmethod
    def _mapping(value: object) -> Mapping[str, object]:
        """将缺失或异常嵌套对象转为空映射，简化来源字段缺失处理。"""
        return value if isinstance(value, Mapping) else {}

    def _authors(self, value: object) -> tuple[CandidateAuthor, ...]:
        """保留 Semantic Scholar 作者数组顺序和来源作者标识。"""
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return ()

        authors: list[CandidateAuthor] = []

        for author in value:
            if not isinstance(author, Mapping):
                continue

            name = self._optional_text(author.get("name"))

            if name is None:
                continue

            authors.append(
                CandidateAuthor(
                    name=name,
                    source_author_id=self._optional_text(author.get("authorId")),
                )
            )

        return tuple(authors)

    def _venue_name(
        self,
        value: object,
        journal: Mapping[str, object],
        publication_venue: Mapping[str, object],
    ) -> str | None:
        """优先采用结构化期刊名，再兼容旧版 ``venue`` 字符串。"""
        return (
            self._optional_text(journal.get("name"))
            or self._optional_text(publication_venue.get("name"))
            or self._optional_text(value)
        )

    def _first_text(self, value: object) -> str | None:
        """从 publicationTypes 数组取得首个有效类型；来源缺失时保留为空。"""
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return None

        for item in value:
            text = self._optional_text(item)

            if text:
                return text

        return None

    @staticmethod
    def _optional_text(value: object) -> str | None:
        """提取非空字符串，避免异常 JSON 类型污染统一候选。"""
        if not isinstance(value, str):
            return None

        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _optional_year(value: object) -> int | None:
        """保留合理的整数年份，拒绝 bool 和来源异常值。"""
        if not isinstance(value, int) or isinstance(value, bool):
            return None

        return value if 1600 <= value <= 2100 else None

    @staticmethod
    def _optional_citation_date(value: object) -> CitationDate | None:
        """解析 Semantic Scholar 的 ``YYYY-MM-DD`` 日期，不完整日期仍可保留年份。"""
        if not isinstance(value, str) or not value.strip():
            return None

        parts = value.strip().split("-")

        try:
            return CitationDate(
                year=int(parts[0]),
                month=int(parts[1]) if len(parts) >= 2 else None,
                day=int(parts[2]) if len(parts) >= 3 else None,
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_non_negative_integer(value: object) -> int | None:
        """只保留非负整数引用计数，供后续展示和排序使用。"""
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
        """构造安全失败结果，不传播上游响应正文或认证信息。"""
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
