"""Durable status and stage values owned by search runs."""

from enum import StrEnum


class SearchRunStatus(StrEnum):
    """Overall status of one multi-provider literature search run."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class SearchRunStage(StrEnum):
    """Deterministic, user-visible processing stage of a search run."""

    DISPATCH = "dispatch"
    PROVIDER_SEARCH = "provider_search"
    NORMALIZE = "normalize"
    TRIAGE = "triage"
    RELEVANCE_ASSESSMENT = "relevance_assessment"
    CITATION_ENRICHMENT = "citation_enrichment"
    COMPLETED = "completed"
