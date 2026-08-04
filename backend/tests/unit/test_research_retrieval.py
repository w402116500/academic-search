"""混合检索中不依赖外部服务的 RRF 规则测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import httpx
import pytest
from app.db.models.document import DocumentChunk
from app.modules.ingestion.embedding import TextEmbedder
from app.modules.research.retrieval import (
    HttpResearchReranker,
    RerankMatch,
    ResearchReranker,
    ResearchRetriever,
    ResearchVectorSearch,
    RetrievedEvidence,
    VectorMatch,
)
from app.modules.research.settings import ResearchSettings
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

_CHUNK_A = UUID("00000000-0000-0000-0000-000000000901")
_CHUNK_B = UUID("00000000-0000-0000-0000-000000000902")


class FakeReranker:
    """以受控分数模拟真实重排器，使检索排序测试不依赖外部网络。"""

    name = "fake_reranker"

    async def rerank(
        self,
        *,
        query: str,
        evidences: object,
        limit: int,
    ) -> tuple[RerankMatch, ...]:
        assert query == "test query"
        assert isinstance(evidences, tuple)
        assert limit == 1
        return (RerankMatch(index=1, score=0.98),)


def _evidence(chunk_id: UUID) -> RetrievedEvidence:
    """创建最小上下文对象，供纯 RRF 融合函数测试。"""
    return RetrievedEvidence(
        chunk_id=chunk_id,
        document_id=UUID("00000000-0000-0000-0000-000000000903"),
        ingestion_run_id=UUID("00000000-0000-0000-0000-000000000904"),
        paper_id=UUID("00000000-0000-0000-0000-000000000905"),
        content="evidence",
        page_start=1,
        page_end=1,
        section_path=(),
        locator={},
        title="Test paper",
        authors=(),
        publication_year=2024,
        source_url=None,
    )


def test_rrf_favors_chunk_retrieved_by_both_vector_and_keyword_paths() -> None:
    """同一片段被两条受控召回路径命中时，RRF 分数应高于单路径片段。"""
    retriever = ResearchRetriever(
        cast(AsyncSession, object()),
        embedder=cast(TextEmbedder, object()),
        vector_search=cast(ResearchVectorSearch, object()),
        settings=ResearchSettings(rag_rrf_k=10),
    )
    contexts = {_CHUNK_A: _evidence(_CHUNK_A), _CHUNK_B: _evidence(_CHUNK_B)}
    lexical_chunk_a = cast(DocumentChunk, SimpleNamespace(id=_CHUNK_A))
    lexical_chunk_b = cast(DocumentChunk, SimpleNamespace(id=_CHUNK_B))

    fused = retriever._fuse(
        vector_matches=(VectorMatch(chunk_id=_CHUNK_A, score=0.9),),
        lexical_rows=[(lexical_chunk_a, 0.7), (lexical_chunk_b, 0.6)],
        contexts=contexts,
    )

    assert fused[0].chunk_id == _CHUNK_A
    assert fused[0].rrf_score is not None
    assert fused[1].rrf_score is not None
    assert fused[0].rrf_score > fused[1].rrf_score


@pytest.mark.asyncio
async def test_disabled_reranker_keeps_rrf_truncation_explicit_and_unscored() -> None:
    """未配置外部服务时不能伪造 `rerank_score` 或把 RRF 截断说成模型精排。"""
    retriever = ResearchRetriever(
        cast(AsyncSession, object()),
        embedder=cast(TextEmbedder, object()),
        vector_search=cast(ResearchVectorSearch, object()),
        settings=ResearchSettings(rag_final_evidence_limit=1),
    )

    final, trace = await retriever._rerank(
        query="test query",
        evidences=(_evidence(_CHUNK_A), _evidence(_CHUNK_B)),
    )

    assert [item.chunk_id for item in final] == [_CHUNK_A]
    assert final[0].rerank_score is None
    assert trace["enabled"] is False
    assert trace["status"] == "disabled"


@pytest.mark.asyncio
async def test_configured_reranker_reorders_evidence_and_sets_real_score() -> None:
    """真实重排适配器的输出，而非 RRF 排名，决定最终证据顺序和分数。"""
    retriever = ResearchRetriever(
        cast(AsyncSession, object()),
        embedder=cast(TextEmbedder, object()),
        vector_search=cast(ResearchVectorSearch, object()),
        settings=ResearchSettings(rag_final_evidence_limit=1),
        reranker=cast(ResearchReranker, FakeReranker()),
    )

    final, trace = await retriever._rerank(
        query="test query",
        evidences=(_evidence(_CHUNK_A), _evidence(_CHUNK_B)),
    )

    assert [item.chunk_id for item in final] == [_CHUNK_B]
    assert final[0].rerank_score == 0.98
    assert trace == {
        "enabled": True,
        "status": "completed",
        "adapter": "fake_reranker",
        "candidate_count": 2,
        "returned_count": 1,
    }


@pytest.mark.asyncio
async def test_http_reranker_uses_configured_endpoint_and_validates_standard_response() -> None:
    """HTTP 适配器实际发送 query/documents 并只接受输入池内的下标结果。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-reranker-key"
        assert request.url == "https://reranker.example.test/v1/rerank"
        payload = json.loads(request.content)
        assert payload["model"] == "test-reranker"
        assert payload["query"] == "test query"
        assert payload["top_n"] == 1
        assert len(payload["documents"]) == 2
        return httpx.Response(200, json={"results": [{"index": 1, "relevance_score": 0.91}]})

    reranker = HttpResearchReranker(
        ResearchSettings(
            rag_reranker_url="https://reranker.example.test/v1/rerank",
            rag_reranker_api_key=SecretStr("test-reranker-key"),
            rag_reranker_model="test-reranker",
        ),
        transport=httpx.MockTransport(handler),
    )
    result = await reranker.rerank(
        query="test query",
        evidences=(_evidence(_CHUNK_A), _evidence(_CHUNK_B)),
        limit=1,
    )

    assert result == (RerankMatch(index=1, score=0.91),)
