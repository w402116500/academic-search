"""按用户和研究集合边界执行向量、关键词与父块合并检索。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from app.db.models.collection import CollectionPaper, ResearchCollection
from app.db.models.document import Document, DocumentChunk, IngestionRun
from app.db.models.paper import Paper
from app.modules.ingestion.contracts import IngestionError
from app.modules.ingestion.embedding import TextEmbedder
from app.modules.ingestion.settings import IngestionSettings
from app.modules.research.contracts import ResearchError, ResearchErrorCode
from app.modules.research.settings import ResearchSettings
from pymilvus import MilvusClient
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession


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
    paper_id: UUID
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
    rank: int | None = None
    source_chunk_ids: tuple[UUID, ...] = ()
    parent_merged: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """检索返回的最终证据及不包含原文的大型审计摘要。"""

    evidences: tuple[RetrievedEvidence, ...]
    trace: dict[str, object]


class MilvusResearchVectorSearch:
    """Milvus 只做向量候选召回，原文、版本和权限仍由 PostgreSQL 二次校验。"""

    def __init__(self, settings: IngestionSettings, *, client: MilvusClient | None = None) -> None:
        """允许离线测试注入小型替身，生产运行延迟使用同步 Milvus SDK。"""
        self._collection_name = settings.milvus_collection_name
        self._client = client or MilvusClient(
            uri=settings.milvus_uri,
            token=(settings.milvus_token.get_secret_value() if settings.milvus_token else ""),
        )

    async def search(
        self,
        *,
        embedding: Sequence[float],
        scope: RetrievalScope,
        ingestion_run_ids: Sequence[UUID],
        limit: int,
    ) -> tuple[VectorMatch, ...]:
        """在 SDK 线程中查询，先验证集合向量维度再将 UUID 结果转换为值对象。"""
        if not ingestion_run_ids:
            return ()
        return await asyncio.to_thread(
            self._search_sync,
            tuple(float(value) for value in embedding),
            scope,
            tuple(ingestion_run_ids),
            limit,
        )

    def _search_sync(
        self,
        embedding: tuple[float, ...],
        scope: RetrievalScope,
        ingestion_run_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[VectorMatch, ...]:
        """构造 UUID-only Milvus filter，所有动态值都来自数据库或类型化 scope。"""
        if not self._client.has_collection(collection_name=self._collection_name):
            return ()
        expected_dimension = self._vector_dimension()
        if expected_dimension is not None and len(embedding) != expected_dimension:
            raise ResearchError(
                ResearchErrorCode.NO_RESEARCHABLE_DOCUMENTS,
                "查询嵌入维度与当前文献向量索引不一致，无法安全检索。",
            )
        run_values = ", ".join(f'"{run_id}"' for run_id in ingestion_run_ids)
        expression = (
            f'owner_user_id == "{scope.owner_user_id}" && '
            f'collection_id == "{scope.collection_id}" && '
            f"level == 3 && ingestion_run_id in [{run_values}]"
        )
        raw_results = self._client.search(
            collection_name=self._collection_name,
            data=[list(embedding)],
            anns_field="embedding",
            filter=expression,
            limit=limit,
            output_fields=["chunk_id"],
        )
        if not raw_results:
            return ()
        matches: list[VectorMatch] = []
        for raw_hit in raw_results[0]:
            hit = dict(raw_hit)
            raw_id = hit.get("id") or hit.get("chunk_id")
            if raw_id is None:
                entity = hit.get("entity")
                if isinstance(entity, dict):
                    raw_id = entity.get("chunk_id")
            if raw_id is None:
                continue
            try:
                matches.append(
                    VectorMatch(chunk_id=UUID(str(raw_id)), score=float(hit["distance"]))
                )
            except (KeyError, TypeError, ValueError):
                continue
        return tuple(matches)

    def _vector_dimension(self) -> int | None:
        """从既有集合 schema 读取真实向量维度，避免把错误模型结果送入 search。"""
        description = self._client.describe_collection(collection_name=self._collection_name)
        schema = description.get("schema", {}) if isinstance(description, dict) else {}
        fields = schema.get("fields", []) if isinstance(schema, dict) else []
        for field in fields:
            if not isinstance(field, dict) or field.get("name") != "embedding":
                continue
            params = field.get("params", {})
            if not isinstance(params, dict):
                return None
            raw_dimension = params.get("dim")
            try:
                return int(raw_dimension) if raw_dimension is not None else None
            except (TypeError, ValueError):
                return None
        return None


class ResearchRetriever:
    """将 Milvus 召回与 PostgreSQL 关键词检索融合，并恢复父块上下文。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        embedder: TextEmbedder,
        vector_search: ResearchVectorSearch,
        settings: ResearchSettings,
    ) -> None:
        self._session = session
        self._embedder = embedder
        self._vector_search = vector_search
        self._settings = settings

    async def retrieve(self, *, scope: RetrievalScope, query: str) -> RetrievalResult:
        """在固定权限范围内完成向量/关键词召回、RRF 与父块 Auto-merging。"""
        ingestion_run_ids = await self._current_ingestion_run_ids(scope)
        if not ingestion_run_ids:
            raise ResearchError(
                ResearchErrorCode.NO_RESEARCHABLE_DOCUMENTS,
                "当前研究集合没有可检索的当前文档版本。",
            )
        try:
            embedding = await self._embedder.embed_query(query)
        except IngestionError as exc:
            raise ResearchError(
                ResearchErrorCode.NO_RESEARCHABLE_DOCUMENTS,
                "查询向量生成失败，当前无法检索文献证据。",
            ) from exc

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
                    + [chunk.id for chunk, _score in lexical_rows]
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
        final = tuple(
            replace(evidence, rank=index)
            for index, evidence in enumerate(
                eligible[: self._settings.rag_final_evidence_limit], start=1
            )
        )
        await self._session.rollback()
        return RetrievalResult(
            evidences=final,
            trace={
                "vector_candidate_count": len(vector_matches),
                "keyword_candidate_count": len(lexical_rows),
                "rrf_candidate_count": len(fused),
                "parent_merged_count": sum(item.parent_merged for item in merged),
                "final_evidence_count": len(final),
                "ingestion_run_count": len(ingestion_run_ids),
            },
        )

    async def _current_ingestion_run_ids(self, scope: RetrievalScope) -> tuple[UUID, ...]:
        """从 PostgreSQL 取得当前完成版本，Milvus 永远不能自行决定可检索范围。"""
        rows = await self._session.scalars(
            select(IngestionRun.id)
            .join(Document, Document.id == IngestionRun.document_id)
            .join(
                CollectionPaper,
                and_(
                    CollectionPaper.collection_id == Document.collection_id,
                    CollectionPaper.paper_id == Document.paper_id,
                ),
            )
            .join(ResearchCollection, ResearchCollection.id == Document.collection_id)
            .where(
                ResearchCollection.id == scope.collection_id,
                ResearchCollection.owner_user_id == scope.owner_user_id,
                ResearchCollection.status == "active",
                CollectionPaper.status == "active",
                IngestionRun.status == "completed",
                IngestionRun.is_current.is_(True),
            )
        )
        return tuple(rows)

    async def _keyword_rows(
        self, *, ingestion_run_ids: Sequence[UUID], query: str
    ) -> tuple[tuple[DocumentChunk, float], ...]:
        """使用 PostgreSQL 全文检索提供可审计的关键词候选，不扫描 Milvus 原文。"""
        query_expression = func.websearch_to_tsquery("simple", query)
        score_expression = func.ts_rank_cd(
            func.to_tsvector("simple", DocumentChunk.content), query_expression
        ).label("lexical_score")
        rows = await self._session.execute(
            select(DocumentChunk, score_expression)
            .where(
                DocumentChunk.ingestion_run_id.in_(ingestion_run_ids),
                DocumentChunk.level == 3,
                score_expression > 0,
            )
            .order_by(score_expression.desc(), DocumentChunk.ordinal)
            .limit(self._settings.rag_keyword_candidate_limit)
        )
        return tuple((chunk, float(score)) for chunk, score in rows)

    async def _load_contexts(
        self,
        *,
        chunk_ids: Sequence[UUID],
        scope: RetrievalScope,
        ingestion_run_ids: Sequence[UUID],
    ) -> dict[UUID, RetrievedEvidence]:
        """二次校验命中的 PostgreSQL 块，拒绝 Milvus 中的旧向量或越界返回。"""
        if not chunk_ids:
            return {}
        rows = await self._session.execute(
            select(DocumentChunk, Document, Paper)
            .join(IngestionRun, IngestionRun.id == DocumentChunk.ingestion_run_id)
            .join(Document, Document.id == IngestionRun.document_id)
            .join(Paper, Paper.id == Document.paper_id)
            .join(
                CollectionPaper,
                and_(
                    CollectionPaper.collection_id == Document.collection_id,
                    CollectionPaper.paper_id == Document.paper_id,
                ),
            )
            .join(ResearchCollection, ResearchCollection.id == Document.collection_id)
            .where(
                DocumentChunk.id.in_(chunk_ids),
                DocumentChunk.ingestion_run_id.in_(ingestion_run_ids),
                ResearchCollection.id == scope.collection_id,
                ResearchCollection.owner_user_id == scope.owner_user_id,
                ResearchCollection.status == "active",
                CollectionPaper.status == "active",
            )
        )
        return {
            chunk.id: RetrievedEvidence(
                chunk_id=chunk.id,
                document_id=document.id,
                ingestion_run_id=chunk.ingestion_run_id,
                paper_id=paper.id,
                content=chunk.content,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_path=tuple(chunk.section_path or ()),
                locator=dict(chunk.locator),
                title=paper.title,
                authors=tuple(dict(author) for author in paper.authors),
                publication_year=paper.publication_year,
                source_url=document.source_url,
            )
            for chunk, document, paper in rows
        }

    def _fuse(
        self,
        *,
        vector_matches: Sequence[VectorMatch],
        lexical_rows: Sequence[tuple[DocumentChunk, float]],
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
        for rank, (chunk, lexical_score) in enumerate(lexical_rows, start=1):
            if chunk.id not in contexts:
                continue
            scores[chunk.id]["lexical_score"] = lexical_score
            scores[chunk.id]["rrf_score"] = scores[chunk.id].get("rrf_score", 0) + 1 / (
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
        parent_rows = await self._session.execute(
            select(DocumentChunk.id, DocumentChunk.parent_chunk_id).where(
                DocumentChunk.id.in_([item.chunk_id for item in evidences])
            )
        )
        grouped: dict[UUID, list[RetrievedEvidence]] = defaultdict(list)
        for chunk_id, parent_chunk_id in parent_rows:
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
