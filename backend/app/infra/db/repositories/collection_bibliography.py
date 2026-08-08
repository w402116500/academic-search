"""SQLAlchemy persistence for research collection bibliography entries."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.collection import CollectionBibliographyEntry, ResearchCollection
from app.infra.db.models.document import Document
from app.modules.research.bibliography import (
    BibliographyContentStatus,
    CollectionBibliographyEntryDraft,
    CollectionBibliographyEntryResult,
    CollectionBibliographyError,
    CollectionBibliographyErrorCode,
    CollectionBibliographyUpsertStatus,
)


class SqlAlchemyCollectionBibliographyRepository:
    """Persist selected candidate metadata without creating global Paper facts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_from_candidate(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        draft: CollectionBibliographyEntryDraft,
    ) -> CollectionBibliographyEntryResult:
        """Create or return the collection-owned entry for one selected candidate."""
        if draft.citation_text is not None and draft.citation_status.value != "ready":
            raise ValueError("citation_text is only valid for ready bibliography metadata")

        if self._session.in_transaction():
            try:
                result = await self._upsert_from_candidate(
                    owner_user_id=owner_user_id,
                    collection_id=collection_id,
                    draft=draft,
                )
            except Exception:
                await self._session.rollback()
                raise
            await self._session.commit()
            return result

        async with self._session.begin():
            return await self._upsert_from_candidate(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                draft=draft,
            )

    async def _upsert_from_candidate(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        draft: CollectionBibliographyEntryDraft,
    ) -> CollectionBibliographyEntryResult:
        await self._require_active_collection(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
        )
        existing = await self._existing_entry(
            collection_id=collection_id,
            source_search_run_id=draft.source_search_run_id,
            source_candidate_id=draft.source_candidate_id,
        )
        if existing is not None:
            return await self._result(
                entry=existing,
                status=CollectionBibliographyUpsertStatus.ALREADY_PRESENT,
            )

        entry = CollectionBibliographyEntry(
            id=uuid4(),
            collection_id=collection_id,
            source_search_run_id=draft.source_search_run_id,
            source_candidate_id=draft.source_candidate_id,
            paper_id=draft.paper_id,
            candidate_title=draft.title,
            candidate_authors=[dict(author) for author in draft.authors],
            candidate_abstract=draft.abstract,
            candidate_publication_year=draft.publication_year,
            candidate_venue=draft.venue,
            candidate_doi=draft.doi,
            candidate_source_url=draft.source_url,
            source_record=dict(draft.source_record),
            citation_status=draft.citation_status.value,
            citation_text=draft.citation_text,
            citation_snapshot=dict(draft.citation_snapshot),
            pdf_status=draft.pdf_status.value,
            pdf_source_url=draft.pdf_source_url,
            pdf_snapshot=dict(draft.pdf_snapshot),
            content_status=draft.content_status.value,
        )
        self._session.add(entry)
        await self._session.flush()
        return CollectionBibliographyEntryResult(
            status=CollectionBibliographyUpsertStatus.ADDED,
            entry_id=entry.id,
            collection_id=entry.collection_id,
            paper_id=entry.paper_id,
            content_status=BibliographyContentStatus(entry.content_status),
        )

    async def _require_active_collection(
        self, *, owner_user_id: UUID, collection_id: UUID
    ) -> ResearchCollection:
        collection = await self._session.scalar(
            select(ResearchCollection)
            .where(
                ResearchCollection.id == collection_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status == "active",
            )
            .with_for_update()
        )
        if collection is None:
            raise CollectionBibliographyError(
                CollectionBibliographyErrorCode.COLLECTION_NOT_FOUND,
                "研究集合不存在、已归档或不属于当前用户。",
            )
        return collection

    async def _existing_entry(
        self,
        *,
        collection_id: UUID,
        source_search_run_id: UUID | None,
        source_candidate_id: UUID | None,
    ) -> CollectionBibliographyEntry | None:
        if source_search_run_id is None or source_candidate_id is None:
            return None
        return await self._session.scalar(
            select(CollectionBibliographyEntry)
            .where(
                CollectionBibliographyEntry.collection_id == collection_id,
                CollectionBibliographyEntry.source_search_run_id == source_search_run_id,
                CollectionBibliographyEntry.source_candidate_id == source_candidate_id,
            )
            .with_for_update()
        )

    async def _result(
        self,
        *,
        entry: CollectionBibliographyEntry,
        status: CollectionBibliographyUpsertStatus,
    ) -> CollectionBibliographyEntryResult:
        document_id = await self._session.scalar(
            select(Document.id)
            .where(Document.bibliography_entry_id == entry.id)
            .order_by(Document.created_at.asc())
            .limit(1)
        )
        return CollectionBibliographyEntryResult(
            status=status,
            entry_id=entry.id,
            collection_id=entry.collection_id,
            paper_id=entry.paper_id,
            document_id=document_id,
            content_status=BibliographyContentStatus(entry.content_status),
        )
