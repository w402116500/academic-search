"""Research-owned use-case contract for collection document builds."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.research.build_contracts import (
    CollectionBuildResponse,
    CollectionDocumentRemovalResponse,
    CollectionDocumentsResponse,
)


class CollectionBuildUseCases(Protocol):
    async def list_documents(
        self, *, owner_user_id: UUID, collection_id: UUID
    ) -> CollectionDocumentsResponse: ...

    async def build(
        self, *, owner_user_id: UUID, collection_id: UUID
    ) -> CollectionBuildResponse: ...

    async def retry_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        ingestion_run_id: UUID,
    ) -> CollectionBuildResponse: ...

    async def remove_pending_document(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        document_id: UUID,
    ) -> CollectionDocumentRemovalResponse: ...

    async def refresh_collection_stage_for_ingestion_run(self, ingestion_run_id: UUID) -> None: ...
