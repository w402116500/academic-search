"""混合检索中不依赖外部服务的 RRF 规则测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from uuid import UUID

from app.db.models.document import DocumentChunk
from app.modules.ingestion.embedding import TextEmbedder
from app.modules.research.retrieval import (
    ResearchRetriever,
    ResearchVectorSearch,
    RetrievedEvidence,
    VectorMatch,
)
from app.modules.research.settings import ResearchSettings
from sqlalchemy.ext.asyncio import AsyncSession

_CHUNK_A = UUID("00000000-0000-0000-0000-000000000901")
_CHUNK_B = UUID("00000000-0000-0000-0000-000000000902")


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
