"""Object-storage ports owned by document acquisition and ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Protocol


class FulltextStorageError(RuntimeError):
    """A document object could not be persisted, promoted, read, or deleted."""


class StagingObjectStorage(Protocol):
    async def upload_pdf(
        self,
        *,
        object_key: str,
        file: BinaryIO,
        sha256: str,
    ) -> None: ...


class ResearchDocumentObjectStorage(Protocol):
    async def promote_staged_pdf(
        self,
        *,
        staging_object_key: str,
        document_object_key: str,
        sha256: str,
    ) -> None: ...

    async def delete_object(self, *, object_key: str) -> None: ...


class ReadableResearchDocumentObjectStorage(ResearchDocumentObjectStorage, Protocol):
    async def download_object_to_file(self, *, object_key: str, destination: Path) -> None: ...
