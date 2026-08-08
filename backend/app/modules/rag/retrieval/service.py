"""按用户和研究集合边界执行向量、关键词与父块合并检索。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from app.modules.rag.ingestion.contracts import IngestionError
from app.modules.rag.ingestion.embedding import TextEmbedder


class RetrievalSettings(Protocol):
    """Behavioral limits required by hybrid retrieval."""

    rag_vector_candidate_limit: int
    rag_keyword_candidate_limit: int
    rag_final_evidence_limit: int
    rag_rrf_k: int
    rag_min_rrf_score: float
    rag_parent_merge_min_hits: int
    rag_reranker_candidate_limit: int


class RetrievalUnavailableError(RuntimeError):
    """The authorized collection cannot currently produce retrieval evidence."""

    code = "research_no_researchable_documents"


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    """一次检索的不可变权限与集合范围，不能由模型工具修改。"""

    owner_user_id: UUID
    collection_id: UUID


@dataclass(frozen=True, slots=True)
class VectorMatch:
    """Milvus 返回的 L3 片段标识与余弦相似度。"""

    chunk_id: UUID
    score: float


@dataclass(frozen=True, slots=True)
class LexicalMatch:
    chunk_id: UUID
    score: float


@dataclass(frozen=True, slots=True)
class RerankMatch:
    """真实重排服务返回的输入序号与相关性分数。"""

    index: int
    score: float


class ResearchRerankerError(RuntimeError):
    """配置已启用的外部重排器未能返回可审计结果时抛出。"""


class ResearchReranker(Protocol):
    """可替换的真实重排器边界；未配置时检索器不会调用它。"""

    @property
    def name(self) -> str:
        """返回会写入 trace 的适配器标识，不包含端点或密钥。"""
        raise NotImplementedError

    async def rerank(
        self,
        *,
        query: str,
        evidences: Sequence[RetrievedEvidence],
        limit: int,
    ) -> tuple[RerankMatch, ...]:
        """对传入的有限证据池重排，只能按输入下标返回结果。"""
        raise NotImplementedError


class ResearchVectorSearch(Protocol):
    """研究检索依赖的最小向量读取边界。"""

    async def search(
        self,
        *,
        embedding: Sequence[float],
        scope: RetrievalScope,
        ingestion_run_ids: Sequence[UUID],
        limit: int,
    ) -> tuple[VectorMatch, ...]:
        """仅返回符合预过滤条件的当前 L3 片段。"""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    """RAG 图可消费且可直接持久化为引用审计记录的原文证据。"""

    chunk_id: UUID
    document_id: UUID
    ingestion_run_id: UUID
    paper_id: UUID | None
    content: str
    page_start: int | None
    page_end: int | None
    section_path: tuple[str, ...]
    locator: dict[str, object]
    title: str
    authors: tuple[dict[str, object], ...]
    publication_year: int | None
    source_url: str | None
    vector_score: float | None = None
    lexical_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    rank: int | None = None
    source_chunk_ids: tuple[UUID, ...] = ()
    parent_merged: bool = False


class ResearchRetrievalRepository(Protocol):
    """PostgreSQL facts required by the hybrid retrieval use case."""

    async def current_ingestion_run_ids(self, scope: RetrievalScope) -> tuple[UUID, ...]: ...

    async def keyword_matches(
        self,
        *,
        ingestion_run_ids: Sequence[UUID],
        query: str,
        limit: int,
    ) -> tuple[LexicalMatch, ...]: ...

    async def load_evidences(
        self,
        *,
        chunk_ids: Sequence[UUID],
        scope: RetrievalScope,
        ingestion_run_ids: Sequence[UUID],
    ) -> dict[UUID, RetrievedEvidence]: ...

    async def parent_ids(self, chunk_ids: Sequence[UUID]) -> dict[UUID, UUID | None]: ...


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """检索返回的最终证据及不包含原文的大型审计摘要。"""

    evidences: tuple[RetrievedEvidence, ...]
    trace: dict[str, object]


class ResearchRetriever:
    """将 Milvus 召回与 PostgreSQL 关键词检索融合，并恢复父块上下文。"""

    def __init__(
        self,
        repository: ResearchRetrievalRepository,
        *,
        embedder: TextEmbedder,
        vector_search: ResearchVectorSearch,
        settings: RetrievalSettings,
        reranker: ResearchReranker | None = None,
    ) -> None:
        self._repository = repository
        self._embedder = embedder
        self._vector_search = vector_search
        self._settings = settings
        self._reranker = reranker

    async def retrieve(self, *, scope: RetrievalScope, query: str) -> RetrievalResult:
        """在固定权限范围内完成向量/关键词召回、RRF 与父块 Auto-merging。"""
        ingestion_run_ids = await self._current_ingestion_run_ids(scope)
        if not ingestion_run_ids:
            raise RetrievalUnavailableError("当前研究集合没有可检索的当前文档版本。")
        try:
            embedding = await self._embedder.embed_query(query)
        except IngestionError as exc:
            raise RetrievalUnavailableError("查询向量生成失败，当前无法检索文献证据。") from exc

        vector_matches, lexical_rows = await asyncio.gather(
            self._vector_search.search(
                embedding=embedding,
                scope=scope,
                ingestion_run_ids=ingestion_run_ids,
                limit=self._settings.rag_vector_candidate_limit,
            ),
            self._keyword_rows(ingestion_run_ids=ingestion_run_ids, query=query),
        )
        contexts = await self._load_contexts(
            chunk_ids=tuple(
                dict.fromkeys(
                    [match.chunk_id for match in vector_matches]
                    + [match.chunk_id for match in lexical_rows]
                )
            ),
            scope=scope,
            ingestion_run_ids=ingestion_run_ids,
        )
        fused = self._fuse(
            vector_matches=vector_matches,
            lexical_rows=lexical_rows,
            contexts=contexts,
        )
        merged = await self._auto_merge(fused, contexts, scope, ingestion_run_ids)
        eligible = tuple(
            evidence
            for evidence in merged
            if (evidence.rrf_score or 0) >= self._settings.rag_min_rrf_score
        )
        reranked, reranker_trace = await self._rerank(query=query, evidences=eligible)
        final = tuple(
            replace(evidence, rank=index) for index, evidence in enumerate(reranked, start=1)
        )
        return RetrievalResult(
            evidences=final,
            trace={
                "vector_candidate_count": len(vector_matches),
                "keyword_candidate_count": len(lexical_rows),
                "rrf_candidate_count": len(fused),
                "parent_merged_count": sum(item.parent_merged for item in merged),
                "reranker": reranker_trace,
                "final_evidence_count": len(final),
                "ingestion_run_count": len(ingestion_run_ids),
            },
        )

    async def _rerank(
        self,
        *,
        query: str,
        evidences: Sequence[RetrievedEvidence],
    ) -> tuple[tuple[RetrievedEvidence, ...], dict[str, object]]:
        """只在真实服务完整配置时计算重排分数；否则保留明确的 RRF 截断语义。"""
        if self._reranker is None:
            return (
                tuple(evidences[: self._settings.rag_final_evidence_limit]),
                {
                    "enabled": False,
                    "status": "disabled",
                    "reason": "未配置真实 Reranker，最终证据按 RRF 结果截断。",
                },
            )
        pool = tuple(evidences[: self._settings.rag_reranker_candidate_limit])
        try:
            matches = await self._reranker.rerank(
                query=query,
                evidences=pool,
                limit=self._settings.rag_final_evidence_limit,
            )
        except ResearchRerankerError as exc:
            fallback = tuple(evidences[: self._settings.rag_final_evidence_limit])
            return (
                fallback,
                {
                    "enabled": True,
                    "status": "failed_fallback",
                    "adapter": self._reranker.name,
                    "candidate_count": len(pool),
                    "returned_count": len(fallback),
                    "reason": "真实 Reranker 调用失败，已按 RRF 结果降级。",
                    "failure_type": exc.__class__.__name__,
                },
            )
        reranked = tuple(replace(pool[match.index], rerank_score=match.score) for match in matches)
        return (
            reranked,
            {
                "enabled": True,
                "status": "completed",
                "adapter": self._reranker.name,
                "candidate_count": len(pool),
                "returned_count": len(reranked),
            },
        )

    async def _current_ingestion_run_ids(self, scope: RetrievalScope) -> tuple[UUID, ...]:
        """从 PostgreSQL 取得当前完成版本，Milvus 永远不能自行决定可检索范围。"""
        return await self._repository.current_ingestion_run_ids(scope)

    async def _keyword_rows(
        self, *, ingestion_run_ids: Sequence[UUID], query: str
    ) -> tuple[LexicalMatch, ...]:
        """使用 PostgreSQL 全文检索提供可审计的关键词候选，不扫描 Milvus 原文。"""
        return await self._repository.keyword_matches(
            ingestion_run_ids=ingestion_run_ids,
            query=query,
            limit=self._settings.rag_keyword_candidate_limit,
        )

    async def _load_contexts(
        self,
        *,
        chunk_ids: Sequence[UUID],
        scope: RetrievalScope,
        ingestion_run_ids: Sequence[UUID],
    ) -> dict[UUID, RetrievedEvidence]:
        """二次校验命中的 PostgreSQL 块，拒绝 Milvus 中的旧向量或越界返回。"""
        return await self._repository.load_evidences(
            chunk_ids=chunk_ids,
            scope=scope,
            ingestion_run_ids=ingestion_run_ids,
        )

    def _fuse(
        self,
        *,
        vector_matches: Sequence[VectorMatch],
        lexical_rows: Sequence[LexicalMatch],
        contexts: dict[UUID, RetrievedEvidence],
    ) -> tuple[RetrievedEvidence, ...]:
        """以 RRF 融合两类候选；分数保留在证据对象中供后续审计。"""
        scores: dict[UUID, dict[str, float]] = defaultdict(dict)
        for rank, match in enumerate(vector_matches, start=1):
            if match.chunk_id not in contexts:
                continue
            scores[match.chunk_id]["vector_score"] = match.score
            scores[match.chunk_id]["rrf_score"] = scores[match.chunk_id].get("rrf_score", 0) + 1 / (
                self._settings.rag_rrf_k + rank
            )
        for rank, match in enumerate(lexical_rows, start=1):
            if match.chunk_id not in contexts:
                continue
            scores[match.chunk_id]["lexical_score"] = match.score
            scores[match.chunk_id]["rrf_score"] = scores[match.chunk_id].get("rrf_score", 0) + 1 / (
                self._settings.rag_rrf_k + rank
            )
        fused = [
            replace(
                contexts[chunk_id],
                vector_score=values.get("vector_score"),
                lexical_score=values.get("lexical_score"),
                rrf_score=values.get("rrf_score"),
                source_chunk_ids=(chunk_id,),
            )
            for chunk_id, values in scores.items()
        ]
        return tuple(
            sorted(
                fused,
                key=lambda evidence: (evidence.rrf_score or 0, evidence.vector_score or 0),
                reverse=True,
            )
        )

    async def _auto_merge(
        self,
        evidences: Sequence[RetrievedEvidence],
        contexts: dict[UUID, RetrievedEvidence],
        scope: RetrievalScope,
        ingestion_run_ids: Sequence[UUID],
    ) -> tuple[RetrievedEvidence, ...]:
        """同一 L2 父块命中达到阈值时以父块替换子块，避免回答失去论证上下文。"""
        if len(evidences) < self._settings.rag_parent_merge_min_hits:
            return tuple(evidences)
        parent_ids = await self._repository.parent_ids([item.chunk_id for item in evidences])
        grouped: dict[UUID, list[RetrievedEvidence]] = defaultdict(list)
        for chunk_id, parent_chunk_id in parent_ids.items():
            if parent_chunk_id is not None and chunk_id in contexts:
                grouped[parent_chunk_id].append(contexts[chunk_id])
        eligible_parent_ids = [
            parent_id
            for parent_id, children in grouped.items()
            if len(children) >= self._settings.rag_parent_merge_min_hits
        ]
        if not eligible_parent_ids:
            return tuple(evidences)
        parent_contexts = await self._load_contexts(
            chunk_ids=eligible_parent_ids,
            scope=scope,
            ingestion_run_ids=ingestion_run_ids,
        )
        children_to_replace = {
            child.chunk_id
            for parent_id in eligible_parent_ids
            for child in grouped[parent_id]
            if parent_id in parent_contexts
        }
        merged: list[RetrievedEvidence] = [
            evidence for evidence in evidences if evidence.chunk_id not in children_to_replace
        ]
        for parent_id in eligible_parent_ids:
            parent = parent_contexts.get(parent_id)
            children = grouped[parent_id]
            if parent is None:
                continue
            merged.append(
                replace(
                    parent,
                    vector_score=max((item.vector_score or 0 for item in children), default=0),
                    lexical_score=max((item.lexical_score or 0 for item in children), default=0),
                    rrf_score=sum(item.rrf_score or 0 for item in children),
                    source_chunk_ids=tuple(item.chunk_id for item in children),
                    parent_merged=True,
                )
            )
        return tuple(sorted(merged, key=lambda item: item.rrf_score or 0, reverse=True))
