"""Text-embedding port owned by the RAG ingestion domain."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class TextEmbedder(Protocol):
    """Generate document and query vectors without exposing an SDK client."""

    async def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...

    async def embed_query(self, text: str) -> tuple[float, ...]: ...
