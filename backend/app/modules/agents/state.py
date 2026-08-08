"""LangGraph checkpoint 状态和研究图输出。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast
from uuid import UUID

from app.modules.rag.retrieval import RetrievedEvidence
from app.modules.research.contracts import ResearchRunStage, ResearchRunStatus


class SingleRagState(TypedDict):
    """单轮图持久化到 LangGraph checkpoint 的 JSON 兼容状态。"""

    question: str
    query: str
    rewrite_count: int
    evidences: list[dict[str, object]]
    retrieval_trace: dict[str, object]
    route: Literal["answer", "rewrite", "clarify", "repair"]
    answer: str
    cited_refs: list[str]
    cited_chunk_ids: list[str]
    repair_count: int
    answer_claim_verification: dict[str, object]
    clarification_question: str


@dataclass(frozen=True, slots=True)
class ResearchGraphOutcome:
    """图执行结束后交给持久化服务的无 ORM 输出。"""

    status: ResearchRunStatus
    stage: ResearchRunStage
    answer: str
    evidences: tuple[RetrievedEvidence, ...]
    cited_chunk_ids: tuple[UUID, ...]
    retrieval_trace: dict[str, Any]
    mode: str


def evidence_to_state(evidence: RetrievedEvidence) -> dict[str, object]:
    """将 dataclass 转为 PostgreSQL checkpoint 可序列化的原始字典。"""
    return {
        "chunk_id": str(evidence.chunk_id),
        "document_id": str(evidence.document_id),
        "ingestion_run_id": str(evidence.ingestion_run_id),
        "paper_id": str(evidence.paper_id) if evidence.paper_id is not None else None,
        "content": evidence.content,
        "page_start": evidence.page_start,
        "page_end": evidence.page_end,
        "section_path": list(evidence.section_path),
        "locator": evidence.locator,
        "title": evidence.title,
        "authors": list(evidence.authors),
        "publication_year": evidence.publication_year,
        "source_url": evidence.source_url,
        "vector_score": evidence.vector_score,
        "lexical_score": evidence.lexical_score,
        "rrf_score": evidence.rrf_score,
        "rerank_score": evidence.rerank_score,
        "rank": evidence.rank,
        "source_chunk_ids": [str(item) for item in evidence.source_chunk_ids],
        "parent_merged": evidence.parent_merged,
    }


def evidence_from_state(data: dict[str, object]) -> RetrievedEvidence:
    """从 checkpoint 状态恢复强类型证据，非法状态会在 Worker 中明确失败。"""
    payload = cast(dict[str, Any], data)
    authors = payload.get("authors", [])
    locator = payload.get("locator", {})
    return RetrievedEvidence(
        chunk_id=UUID(str(payload["chunk_id"])),
        document_id=UUID(str(payload["document_id"])),
        ingestion_run_id=UUID(str(payload["ingestion_run_id"])),
        paper_id=UUID(str(payload["paper_id"])) if payload.get("paper_id") is not None else None,
        content=str(payload["content"]),
        page_start=int(payload["page_start"]) if payload.get("page_start") is not None else None,
        page_end=int(payload["page_end"]) if payload.get("page_end") is not None else None,
        section_path=tuple(str(item) for item in payload.get("section_path", [])),
        locator=dict(locator) if isinstance(locator, dict) else {},
        title=str(payload["title"]),
        authors=tuple(dict(item) for item in authors if isinstance(item, dict)),
        publication_year=(
            int(payload["publication_year"])
            if payload.get("publication_year") is not None
            else None
        ),
        source_url=str(payload["source_url"]) if payload.get("source_url") is not None else None,
        vector_score=(
            float(payload["vector_score"]) if payload.get("vector_score") is not None else None
        ),
        lexical_score=(
            float(payload["lexical_score"]) if payload.get("lexical_score") is not None else None
        ),
        rrf_score=float(payload["rrf_score"]) if payload.get("rrf_score") is not None else None,
        rerank_score=(
            float(payload["rerank_score"]) if payload.get("rerank_score") is not None else None
        ),
        rank=int(payload["rank"]) if payload.get("rank") is not None else None,
        source_chunk_ids=tuple(UUID(str(item)) for item in payload.get("source_chunk_ids", [])),
        parent_merged=bool(payload.get("parent_merged", False)),
    )
