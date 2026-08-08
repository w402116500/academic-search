"""SQLAlchemy adapter for collection-scoped research retrieval facts."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.collection import CollectionBibliographyEntry, ResearchCollection
from app.infra.db.models.document import Document, DocumentChunk, IngestionRun
from app.infra.db.models.paper import Paper
from app.modules.rag.retrieval import (
    LexicalMatch,
    RetrievalScope,
    RetrievedEvidence,
)


class SqlAlchemyResearchRetrievalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current_ingestion_run_ids(self, scope: RetrievalScope) -> tuple[UUID, ...]:
        rows = await self._session.scalars(
            select(IngestionRun.id)
            .join(Document, Document.id == IngestionRun.document_id)
            .join(
                CollectionBibliographyEntry,
                and_(
                    CollectionBibliographyEntry.collection_id == Document.collection_id,
                    CollectionBibliographyEntry.id == Document.bibliography_entry_id,
                ),
            )
            .join(ResearchCollection, ResearchCollection.id == Document.collection_id)
            .where(
                ResearchCollection.id == scope.collection_id,
                ResearchCollection.owner_user_id == scope.owner_user_id,
                ResearchCollection.status == "active",
                CollectionBibliographyEntry.status == "active",
                IngestionRun.status == "completed",
                IngestionRun.is_current.is_(True),
            )
        )
        result = tuple(rows)
        await self._session.rollback()
        return result

    async def keyword_matches(
        self,
        *,
        ingestion_run_ids: Sequence[UUID],
        query: str,
        limit: int,
    ) -> tuple[LexicalMatch, ...]:
        query_expression = func.websearch_to_tsquery("simple", query)
        score_expression = func.ts_rank_cd(
            func.to_tsvector("simple", DocumentChunk.content), query_expression
        ).label("lexical_score")
        rows = await self._session.execute(
            select(DocumentChunk.id, score_expression)
            .where(
                DocumentChunk.ingestion_run_id.in_(ingestion_run_ids),
                DocumentChunk.level == 3,
                score_expression > 0,
            )
            .order_by(score_expression.desc(), DocumentChunk.ordinal)
            .limit(limit)
        )
        result = tuple(
            LexicalMatch(chunk_id=chunk_id, score=float(score)) for chunk_id, score in rows
        )
        await self._session.rollback()
        return result

    async def load_evidences(
        self,
        *,
        chunk_ids: Sequence[UUID],
        scope: RetrievalScope,
        ingestion_run_ids: Sequence[UUID],
    ) -> dict[UUID, RetrievedEvidence]:
        if not chunk_ids:
            return {}
        rows = await self._session.execute(
            select(DocumentChunk, Document, CollectionBibliographyEntry, Paper)
            .join(IngestionRun, IngestionRun.id == DocumentChunk.ingestion_run_id)
            .join(Document, Document.id == IngestionRun.document_id)
            .join(
                CollectionBibliographyEntry,
                and_(
                    CollectionBibliographyEntry.collection_id == Document.collection_id,
                    CollectionBibliographyEntry.id == Document.bibliography_entry_id,
                ),
            )
            .outerjoin(Paper, Paper.id == CollectionBibliographyEntry.paper_id)
            .join(ResearchCollection, ResearchCollection.id == Document.collection_id)
            .where(
                DocumentChunk.id.in_(chunk_ids),
                DocumentChunk.ingestion_run_id.in_(ingestion_run_ids),
                ResearchCollection.id == scope.collection_id,
                ResearchCollection.owner_user_id == scope.owner_user_id,
                ResearchCollection.status == "active",
                CollectionBibliographyEntry.status == "active",
            )
        )
        result = {
            chunk.id: RetrievedEvidence(
                chunk_id=chunk.id,
                document_id=document.id,
                ingestion_run_id=chunk.ingestion_run_id,
                paper_id=paper.id if paper is not None else entry.paper_id,
                content=chunk.content,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_path=tuple(chunk.section_path or ()),
                locator=dict(chunk.locator),
                title=paper.title if paper is not None else entry.candidate_title,
                authors=tuple(
                    dict(author)
                    for author in (paper.authors if paper is not None else entry.candidate_authors)
                ),
                publication_year=(
                    paper.publication_year
                    if paper is not None
                    else entry.candidate_publication_year
                ),
                source_url=document.source_url
                or (paper.official_url if paper is not None else entry.candidate_source_url),
            )
            for chunk, document, entry, paper in rows
        }
        await self._session.rollback()
        return result

    async def parent_ids(self, chunk_ids: Sequence[UUID]) -> dict[UUID, UUID | None]:
        rows = await self._session.execute(
            select(DocumentChunk.id, DocumentChunk.parent_chunk_id).where(
                DocumentChunk.id.in_(chunk_ids)
            )
        )
        result = {chunk_id: parent_id for chunk_id, parent_id in rows}
        await self._session.rollback()
        return result
