"""Canonical contract for admitting verified literature into a research collection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from app.modules.literature.contracts import CitationMetadata


class LiteratureAdmissionFulltextDocument(Protocol):
    """Verified document projection needed to promote a staged PDF."""

    @property
    def candidate_id(self) -> UUID: ...

    @property
    def doi(self) -> str: ...

    @property
    def source_url(self) -> str: ...

    @property
    def staging_object_key(self) -> str: ...

    @property
    def original_filename(self) -> str: ...

    @property
    def media_type(self) -> str: ...

    @property
    def byte_size(self) -> int: ...

    @property
    def sha256(self) -> str: ...

    @property
    def origin_kind(self) -> str: ...

    @property
    def access_rights(self) -> str: ...


class LiteratureAdmissionFulltext(Protocol):
    """Structural proof produced by Documents before literature admission."""

    @property
    def candidate_id(self) -> UUID: ...

    @property
    def status(self) -> str: ...

    @property
    def document(self) -> LiteratureAdmissionFulltextDocument | None: ...


@dataclass(frozen=True, slots=True)
class LiteratureAdmissionCandidate:
    """进入严格准入边界所需的最小候选事实集合。"""

    candidate_id: UUID
    doi: str | None
    abstract: str | None
    official_url: str | None
    citation: CitationMetadata | None


class CollectionAdmissionErrorCode(StrEnum):
    """加入研究集合时可安全返回给 API 和前端的失败类别。"""

    COLLECTION_UNAVAILABLE = "collection_unavailable"
    CITATION_NOT_READY = "citation_not_ready"
    FULLTEXT_UNAVAILABLE = "fulltext_unavailable"
    FULLTEXT_MISMATCH = "fulltext_mismatch"
    DUPLICATE_DOCUMENT = "duplicate_document"
    STORAGE_ERROR = "storage_error"
    PERSISTENCE_ERROR = "persistence_error"


class CollectionAdmissionError(RuntimeError):
    """阻止不完整候选进入长期研究库的明确业务异常。"""

    def __init__(
        self,
        code: CollectionAdmissionErrorCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class CollectionAdmissionStatus(StrEnum):
    """文献加入集合后的幂等结果。"""

    ADDED = "added"
    ALREADY_JOINED = "already_joined"


class CollectionAdmissionResult(BaseModel):
    """加入成功或幂等命中后，供 API 与后续 Worker 使用的标识集合。"""

    status: CollectionAdmissionStatus
    collection_id: UUID
    paper_id: UUID
    document_id: UUID | None = None
    ingestion_run_id: UUID | None = None


class LiteratureAdmissionPort(Protocol):
    """Admit one server-verified candidate without exposing persistence details."""

    async def admit(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        candidate: LiteratureAdmissionCandidate,
        fulltext_result: LiteratureAdmissionFulltext,
    ) -> CollectionAdmissionResult: ...
