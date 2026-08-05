"""HTTP adapter for the configured research reranking model."""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from app.modules.rag.retrieval.service import (
    RerankMatch,
    ResearchRerankerError,
    RetrievedEvidence,
)
from app.modules.research.settings import ResearchSettings


class HttpResearchReranker:
    """Call a standard `/rerank` JSON endpoint and validate its evidence indexes."""

    def __init__(
        self,
        settings: ResearchSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.reranker_enabled:
            raise ValueError("未配置 RAG Reranker，不能创建 HTTP 重排适配器")
        self._settings = settings
        self._transport = transport

    @property
    def name(self) -> str:
        return "http_reranker"

    async def rerank(
        self,
        *,
        query: str,
        evidences: Sequence[RetrievedEvidence],
        limit: int,
    ) -> tuple[RerankMatch, ...]:
        if not evidences:
            return ()
        api_key = self._settings.rag_reranker_api_key
        url = self._settings.rag_reranker_url
        model = self._settings.rag_reranker_model
        if api_key is None or url is None or model is None:
            raise ResearchRerankerError("Reranker 配置不完整。")
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.rag_reranker_timeout_seconds),
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
                    json={
                        "model": model,
                        "query": query,
                        "documents": [
                            evidence.content[: self._settings.rag_reranker_document_max_characters]
                            for evidence in evidences
                        ],
                        "top_n": min(limit, len(evidences)),
                        "return_documents": False,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ResearchRerankerError("真实 Reranker 调用失败。") from exc

        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raise ResearchRerankerError("真实 Reranker 返回了无效结果。")
        matches: list[RerankMatch] = []
        seen_indexes: set[int] = set()
        for item in raw_results:
            if not isinstance(item, dict):
                raise ResearchRerankerError("真实 Reranker 返回了无效结果。")
            raw_index = item.get("index")
            raw_score = item.get("relevance_score")
            if isinstance(raw_index, bool) or not isinstance(raw_index, int):
                raise ResearchRerankerError("真实 Reranker 返回了无效证据下标。")
            if raw_index < 0 or raw_index >= len(evidences) or raw_index in seen_indexes:
                raise ResearchRerankerError("真实 Reranker 返回了重复或越界证据下标。")
            if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                raise ResearchRerankerError("真实 Reranker 返回了无效相关性分数。")
            seen_indexes.add(raw_index)
            matches.append(RerankMatch(index=raw_index, score=float(raw_score)))
        if not matches:
            raise ResearchRerankerError("真实 Reranker 没有返回可用证据。")
        return tuple(sorted(matches, key=lambda item: item.score, reverse=True)[:limit])
