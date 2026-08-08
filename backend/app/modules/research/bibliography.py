"""Research-owned contracts for collection bibliography entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel


class CollectionBibliographyEntryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class BibliographyCitationStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    UNAVAILABLE = "unavailable"


class BibliographyPdfStatus(StrEnum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    REQUIRES_UPLOAD = "requires_upload"


class BibliographyContentStatus(StrEnum):
    PENDING_AUTO_DOWNLOAD = "pending_auto_download"
    REQUIRES_UPLOAD = "requires_upload"
    DOCUMENT_READY = "document_ready"
    INGESTING = "ingesting"
    RESEARCHABLE = "researchable"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CollectionBibliographyUpsertStatus(StrEnum):
    ADDED = "added"
    ALREADY_PRESENT = "already_present"


class CollectionBibliographyErrorCode(StrEnum):
    COLLECTION_NOT_FOUND = "collection_bibliography_collection_not_found"


class CollectionBibliographyError(RuntimeError):
    def __init__(self, code: CollectionBibliographyErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CollectionBibliographyEntryDraft:
    """Candidate metadata selected by the user for durable collection storage."""

    source_search_run_id: UUID | None
    source_candidate_id: UUID | None
    title: str
    authors: tuple[dict[str, Any], ...] = ()
    abstract: str | None = None
    publication_year: int | None = None
    venue: str | None = None
    doi: str | None = None
    source_url: str | None = None
    source_record: dict[str, Any] = field(default_factory=dict)
    citation_status: BibliographyCitationStatus = BibliographyCitationStatus.PENDING
    citation_text: str | None = None
    citation_snapshot: dict[str, Any] = field(default_factory=dict)
    pdf_status: BibliographyPdfStatus = BibliographyPdfStatus.UNKNOWN
    pdf_source_url: str | None = None
    pdf_snapshot: dict[str, Any] = field(default_factory=dict)
    content_status: BibliographyContentStatus = BibliographyContentStatus.REQUIRES_UPLOAD
    paper_id: UUID | None = None


class CollectionBibliographyEntryResult(BaseModel):
    status: CollectionBibliographyUpsertStatus
    entry_id: UUID
    collection_id: UUID
    paper_id: UUID | None = None
    document_id: UUID | None = None
    content_status: BibliographyContentStatus


class CollectionBibliographyEntryProjection(BaseModel):
    id: UUID
    collection_id: UUID
    source_search_run_id: UUID | None
    source_candidate_id: UUID | None
    paper_id: UUID | None
    document_id: UUID | None = None
    status: CollectionBibliographyEntryStatus
    title: str
    authors: list[dict[str, Any]]
    abstract: str | None
    publication_year: int | None
    venue: str | None
    doi: str | None
    source_url: str | None
    citation_status: BibliographyCitationStatus
    citation_text: str | None
    pdf_status: BibliographyPdfStatus
    pdf_source_url: str | None
    content_status: BibliographyContentStatus
    automatic_download_attempts: int
    tags: list[str]
    note: str | None
    added_at: datetime


class CollectionBibliographyRepository(Protocol):
    async def upsert_from_candidate(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        draft: CollectionBibliographyEntryDraft,
    ) -> CollectionBibliographyEntryResult: ...
