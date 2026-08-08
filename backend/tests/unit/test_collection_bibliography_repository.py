"""Collection bibliography persistence tests without a live database."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from app.infra.db.models.collection import CollectionBibliographyEntry, ResearchCollection
from app.infra.db.models.paper import Paper
from app.infra.db.repositories.collection_bibliography import (
    SqlAlchemyCollectionBibliographyRepository,
)
from app.modules.research.bibliography import (
    BibliographyCitationStatus,
    BibliographyContentStatus,
    BibliographyPdfStatus,
    CollectionBibliographyEntryDraft,
    CollectionBibliographyUpsertStatus,
)
from sqlalchemy.ext.asyncio import AsyncSession

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000801")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000802")
_SEARCH_RUN_ID = UUID("00000000-0000-0000-0000-000000000803")
_CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000804")
_ENTRY_ID = UUID("00000000-0000-0000-0000-000000000805")
_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000806")


class FakeSession:
    """Return configured scalar values and record ORM additions."""

    def __init__(self, *, scalar_values: list[object | None]) -> None:
        self._scalar_values = iter(scalar_values)
        self.added: list[object] = []
        self.flush_count = 0

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self

    async def scalar(self, _statement: object) -> object | None:
        try:
            return next(self._scalar_values)
        except StopIteration as exc:
            raise AssertionError("测试缺少数据库查询预设值") from exc

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        self.flush_count += 1


def _collection() -> ResearchCollection:
    return ResearchCollection(
        id=_COLLECTION_ID,
        owner_user_id=_OWNER_ID,
        name="Bibliography persistence",
        status="active",
    )


def _draft() -> CollectionBibliographyEntryDraft:
    return CollectionBibliographyEntryDraft(
        source_search_run_id=_SEARCH_RUN_ID,
        source_candidate_id=_CANDIDATE_ID,
        title="Unverified candidate that the user selected",
        authors=({"literal": "A. Candidate"},),
        abstract="Candidate abstract",
        publication_year=2025,
        venue="Candidate Source",
        doi=None,
        source_url="https://example.test/candidate",
        source_record={"provider": "fixture"},
        citation_status=BibliographyCitationStatus.UNAVAILABLE,
        citation_snapshot={"status": "unavailable"},
        pdf_status=BibliographyPdfStatus.REQUIRES_UPLOAD,
        pdf_snapshot={"status": "requires_upload"},
        content_status=BibliographyContentStatus.REQUIRES_UPLOAD,
        paper_id=None,
    )


@pytest.mark.asyncio
async def test_upsert_from_candidate_persists_entry_without_creating_paper() -> None:
    """候选展示元数据可以成为集合书目条目，但不能伪造全局 Paper。"""
    session = FakeSession(scalar_values=[_collection(), None])
    repository = SqlAlchemyCollectionBibliographyRepository(cast(AsyncSession, session))

    result = await repository.upsert_from_candidate(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        draft=_draft(),
    )

    entry = next(item for item in session.added if isinstance(item, CollectionBibliographyEntry))
    assert result.status is CollectionBibliographyUpsertStatus.ADDED
    assert result.entry_id == entry.id
    assert result.paper_id is None
    assert result.document_id is None
    assert entry.paper_id is None
    assert entry.candidate_title == "Unverified candidate that the user selected"
    assert entry.citation_status == "unavailable"
    assert entry.content_status == "requires_upload"
    assert not any(isinstance(item, Paper) for item in session.added)


@pytest.mark.asyncio
async def test_upsert_from_candidate_returns_existing_source_candidate_entry() -> None:
    """同一集合中重复加入同一个来源候选时返回既有条目。"""
    existing = CollectionBibliographyEntry(
        id=_ENTRY_ID,
        collection_id=_COLLECTION_ID,
        source_search_run_id=_SEARCH_RUN_ID,
        source_candidate_id=_CANDIDATE_ID,
        status="active",
        candidate_title="Existing selected candidate",
        candidate_authors=[],
        source_record={},
        citation_status="unavailable",
        citation_snapshot={},
        pdf_status="requires_upload",
        pdf_snapshot={},
        content_status="requires_upload",
        tags=[],
    )
    existing.added_at = datetime.now(UTC)
    existing.automatic_download_attempts = 0
    session = FakeSession(scalar_values=[_collection(), existing, _DOCUMENT_ID])
    repository = SqlAlchemyCollectionBibliographyRepository(cast(AsyncSession, session))

    result = await repository.upsert_from_candidate(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        draft=_draft(),
    )

    assert result.status is CollectionBibliographyUpsertStatus.ALREADY_PRESENT
    assert result.entry_id == _ENTRY_ID
    assert result.document_id == _DOCUMENT_ID
    assert session.added == []
    assert session.flush_count == 0
