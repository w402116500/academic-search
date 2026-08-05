"""Collection-scoped hybrid retrieval contracts and use cases."""

from app.modules.rag.retrieval.service import (
    LexicalMatch,
    RerankMatch,
    ResearchReranker,
    ResearchRerankerError,
    ResearchRetrievalRepository,
    ResearchRetriever,
    ResearchVectorSearch,
    RetrievalResult,
    RetrievalScope,
    RetrievalSettings,
    RetrievalUnavailableError,
    RetrievedEvidence,
    VectorMatch,
)

__all__ = [
    "LexicalMatch",
    "RerankMatch",
    "ResearchReranker",
    "ResearchRerankerError",
    "ResearchRetrievalRepository",
    "ResearchRetriever",
    "ResearchVectorSearch",
    "RetrievalResult",
    "RetrievalSettings",
    "RetrievalScope",
    "RetrievalUnavailableError",
    "RetrievedEvidence",
    "VectorMatch",
]
