"""Milvus adapter for collection-scoped research vector recall."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from pymilvus import MilvusClient

from app.core.ingestion_settings import IngestionSettings
from app.modules.rag.retrieval.service import (
    RetrievalScope,
    RetrievalUnavailableError,
    VectorMatch,
)


class MilvusResearchVectorSearch:
    """Recall vector candidates; PostgreSQL remains the permission and content authority."""

    def __init__(self, settings: IngestionSettings, *, client: MilvusClient | None = None) -> None:
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
        if not self._client.has_collection(collection_name=self._collection_name):
            return ()
        expected_dimension = self._vector_dimension()
        if expected_dimension is not None and len(embedding) != expected_dimension:
            raise RetrievalUnavailableError("查询嵌入维度与当前文献向量索引不一致，无法安全检索。")
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
