"""Persistence scope tests for collection-backed research retrieval."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import pytest
from app.infra.db.models.collection import CollectionBibliographyEntry
from app.infra.db.models.document import Document, DocumentChunk
from app.infra.db.repositories.research_retrieval import (
    SqlAlchemyResearchRetrievalRepository,
)
from app.modules.rag.retrieval import RetrievalScope
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

_OWNER_ID = UUID("00000000-0000-0000-0000-000000001001")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000001002")
_ENTRY_ID = UUID("00000000-0000-0000-0000-000000001003")
_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000001004")
_INGESTION_RUN_ID = UUID("00000000-0000-0000-0000-000000001005")
_CHUNK_ID = UUID("00000000-0000-0000-0000-000000001006")


class FakeRetrievalSession:
    def __init__(
        self,
        *,
        scalar_rows: list[object] | None = None,
        execute_rows: list[tuple[object, ...]] | None = None,
    ) -> None:
        self._scalar_rows = scalar_rows or []
        self._execute_rows = execute_rows or []
        self.scalars_statements: list[object] = []
        self.execute_statements: list[object] = []
        self.rollback_count = 0

    async def scalars(self, statement: object) -> list[object]:
        self.scalars_statements.append(statement)
        return self._scalar_rows

    async def execute(self, statement: object) -> list[tuple[object, ...]]:
        self.execute_statements.append(statement)
        return self._execute_rows

    async def rollback(self) -> None:
        self.rollback_count += 1


def _scope() -> RetrievalScope:
    return RetrievalScope(owner_user_id=_OWNER_ID, collection_id=_COLLECTION_ID)


def _sql(statement: Any) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_current_ingestion_scope_uses_collection_bibliography_entries() -> None:
    """RAG scope is current completed document state, not verified-paper membership."""
    session = FakeRetrievalSession(scalar_rows=[_INGESTION_RUN_ID])

    result = await SqlAlchemyResearchRetrievalRepository(
        cast(AsyncSession, session)
    ).current_ingestion_run_ids(_scope())

    assert result == (_INGESTION_RUN_ID,)
    assert session.rollback_count == 1
    sql = _sql(session.scalars_statements[0])
    assert "collection_bibliography_entries" in sql
    assert "collection_papers" not in sql
    assert "bibliography_entry_id" in sql


@pytest.mark.asyncio
async def test_load_evidences_uses_entry_snapshot_when_paper_is_absent() -> None:
    """Paperless ingested documents keep user-selected metadata instead of fabricating Paper."""
    document = Document(
        id=_DOCUMENT_ID,
        collection_id=_COLLECTION_ID,
        bibliography_entry_id=_ENTRY_ID,
        paper_id=None,
        origin_kind="user_upload",
        original_filename="paper.pdf",
        media_type="application/pdf",
        byte_size=123,
        sha256="a" * 64,
        object_key="objects/paper.pdf",
        source_url=None,
        access_rights="user_upload",
    )
    entry = CollectionBibliographyEntry(
        id=_ENTRY_ID,
        collection_id=_COLLECTION_ID,
        source_search_run_id=None,
        source_candidate_id=None,
        paper_id=None,
        status="active",
        candidate_title="Selected candidate title",
        candidate_authors=[{"literal": "Candidate Author"}],
        candidate_abstract=None,
        candidate_publication_year=2025,
        candidate_venue="Candidate Venue",
        candidate_doi=None,
        candidate_source_url="https://example.test/candidate",
        source_record={},
        citation_status="unavailable",
        citation_text=None,
        citation_snapshot={},
        pdf_status="requires_upload",
        pdf_source_url=None,
        pdf_snapshot={},
        content_status="researchable",
        automatic_download_attempts=0,
        tags=[],
        note=None,
    )
    chunk = DocumentChunk(
        id=_CHUNK_ID,
        ingestion_run_id=_INGESTION_RUN_ID,
        parent_chunk_id=None,
        root_chunk_id=None,
        level=3,
        ordinal=1,
        content="paperless evidence",
        token_count=3,
        page_start=1,
        page_end=1,
        section_path=["Intro"],
        locator={"page": 1},
        content_sha256="b" * 64,
    )
    session = FakeRetrievalSession(execute_rows=[(chunk, document, entry, None)])

    result = await SqlAlchemyResearchRetrievalRepository(
        cast(AsyncSession, session)
    ).load_evidences(
        chunk_ids=[_CHUNK_ID],
        scope=_scope(),
        ingestion_run_ids=[_INGESTION_RUN_ID],
    )

    evidence = result[_CHUNK_ID]
    assert evidence.paper_id is None
    assert evidence.title == "Selected candidate title"
    assert evidence.authors == ({"literal": "Candidate Author"},)
    assert evidence.publication_year == 2025
    assert evidence.source_url == "https://example.test/candidate"
    sql = _sql(session.execute_statements[0])
    assert "collection_bibliography_entries" in sql
    assert "LEFT OUTER JOIN papers" in sql
    assert "collection_papers" not in sql
