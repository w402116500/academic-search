"""独立相关性 Worker 的字段级快照合并测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from app.modules.literature.contracts import (
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
)
from app.modules.search.citation_enrichment import CitationMetadataEnricher
from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateRelevanceAssessment,
    CandidateRelevanceEvidence,
    CandidateRelevanceLevel,
    CandidateRelevanceState,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.search.relevance import CandidateRelevanceTechnicalFailure
from app.modules.search.relevance_execution import CandidateRelevanceRunExecutor
from app.modules.search.run_models import SearchRunRecord
from app.modules.search.run_repository import SearchRunRepository
from app.modules.search.run_service import SearchRunService
from app.modules.search.session import SearchSessionStore
from app.modules.search.state import SearchRunStage


class FakeRelevanceStore:
    """内存快照替身，保留执行器自动恢复所需的原子合并边界。"""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.events: list[dict[str, Any]] = []
        self.released_locks: list[tuple[str, str]] = []

    async def read_snapshot(self, _key: str) -> dict[str, Any]:
        return self.snapshot

    async def merge_snapshot(
        self,
        _key: str,
        transform: Any,
    ) -> dict[str, Any]:
        self.snapshot = transform(self.snapshot)
        return self.snapshot

    async def append_event(self, _key: str, event: dict[str, Any]) -> str:
        self.events.append(event)
        return "1-0"

    async def try_acquire_lock(self, _key: str, *, token: str, ttl_seconds: int) -> bool:
        _ = token, ttl_seconds
        return True

    async def renew_lock(self, _key: str, *, token: str, ttl_seconds: int) -> bool:
        _ = token, ttl_seconds
        return True

    async def refresh_ttl(self, _key: str) -> None:
        return None

    async def renew_arq_in_progress(self, _job_id: str, *, ttl_seconds: int) -> None:
        _ = ttl_seconds
        return None

    async def release_lock(self, key: str, *, token: str) -> None:
        self.released_locks.append((key, token))


class FakeWorkflowService:
    """记录数据库进度更新，避免自动恢复测试接触真实数据库。"""

    def __init__(self) -> None:
        self.progress_updates: list[dict[str, Any]] = []

    async def update_progress(self, **kwargs: Any) -> None:
        self.progress_updates.append(kwargs)


class RecordingRelevanceQueue:
    """记录完整集合的自动重投，不实现任何逐候选接口。"""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, int]] = []

    async def enqueue_relevance(self, *, search_run_id: UUID, attempt_no: int) -> str:
        self.calls.append((search_run_id, attempt_no))
        return f"relevance-{search_run_id}-{attempt_no}"


class FakeSearchRunRepository:
    """只返回当前运行，供过期尝试的早退路径读取。"""

    def __init__(self, run: SearchRunRecord) -> None:
        self.run = run

    async def get_relevance_run_for_update(self, search_run_id: UUID) -> SearchRunRecord | None:
        return self.run if self.run.id == search_run_id else None


def _candidate() -> UnifiedCandidate:
    source = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id="relevance-merge",
        title="Sleep quality and mental health",
        abstract="The study examines sleep quality and mental health outcomes.",
    )
    return UnifiedCandidate(
        doi="10.1000/relevance.merge",
        title=source.title,
        title_key="sleep quality mental health",
        authors=(CandidateAuthor(name="Ada Lovelace"),),
        abstract=source.abstract,
        source_records=(source,),
        triage=TriageDecision(included=True),
        citation=CitationMetadata(
            status=CitationMetadataStatus.READY,
            authors=(CitationAuthor(given="Ada", family="Lovelace"),),
            title=source.title,
            document_type="journal_article",
            issued_date=CitationDate(year=2024),
            doi="10.1000/relevance.merge",
            url="https://doi.org/10.1000/relevance.merge",
        ),
    )


def _run() -> SearchRunRecord:
    now = datetime.now(UTC)
    return SearchRunRecord(
        id=UUID("00000000-0000-0000-0000-000000003001"),
        collection_id=UUID("00000000-0000-0000-0000-000000003002"),
        research_plan_id=UUID("00000000-0000-0000-0000-000000003003"),
        arq_job_id=None,
        redis_session_key="academic-search:search-run:00000000-0000-0000-0000-000000003001",
        status="running",
        stage="relevance_assessment",
        attempt_no=1,
        provider_summary={},
        candidate_counts={},
        error_code=None,
        error_message=None,
        started_at=now,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )


def _snapshot(*candidates: UnifiedCandidate, attempt_no: int = 1) -> dict[str, Any]:
    return {
        "status": "running",
        "stage": "relevance_assessment",
        "relevance_attempt_no": attempt_no,
        "candidate_counts": {"candidate_count": len(candidates)},
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
    }


def _with_level(candidate: UnifiedCandidate, level: CandidateRelevanceLevel) -> UnifiedCandidate:
    return candidate.model_copy(
        update={
            "relevance_state": CandidateRelevanceState.COMPLETED,
            "relevance_assessment": CandidateRelevanceAssessment(
                level=level,
                study_focus="测试相关性层级。",
                reason="测试相关性层级。",
                helpful_aspect="测试相关性层级。",
                recommendation="测试。",
                evidence=(CandidateRelevanceEvidence(source_field="title", quote=candidate.title),),
            ),
        }
    )


def test_relevance_merge_only_replaces_relevance_fields() -> None:
    """模型完成时不覆盖同时写入的题录、候选原文或准备清单。"""
    candidate = _candidate()
    assessed = candidate.model_copy(
        update={
            "relevance_state": CandidateRelevanceState.COMPLETED,
            "relevance_assessment": CandidateRelevanceAssessment(
                level=CandidateRelevanceLevel.CORE,
                study_focus="研究睡眠质量与心理健康。",
                reason="标题和摘要都直接涉及这两个变量。",
                helpful_aspect="可用于分析两者关系。",
                recommendation="建议优先核验全文。",
                evidence=(
                    CandidateRelevanceEvidence(
                        source_field="title", quote="Sleep quality and mental health"
                    ),
                ),
            ),
        }
    )
    snapshot = {
        "status": "running",
        "stage": "relevance_assessment",
        "candidate_counts": {},
        "candidates": [candidate.model_dump(mode="json")],
        "candidate_selection": {"selected_candidate_ids": [str(candidate.candidate_id)]},
    }

    merged = CandidateRelevanceRunExecutor._merge_relevance(snapshot, (assessed,))
    result = UnifiedCandidate.model_validate(merged["candidates"][0])

    assert result.citation == candidate.citation
    assert result.title == candidate.title
    assert result.source_records == candidate.source_records
    assert result.relevance_state is CandidateRelevanceState.COMPLETED
    assert result.relevance_assessment == assessed.relevance_assessment
    assert merged["candidate_selection"] == snapshot["candidate_selection"]


@pytest.mark.asyncio
async def test_first_technical_failure_queues_one_full_collection_retry() -> None:
    """首轮技术失败只安排第 2 次完整集合任务，不重调 Provider 或拆分候选。"""
    run = _run()
    candidate = _candidate()
    store = FakeRelevanceStore(_snapshot(candidate))
    queue = RecordingRelevanceQueue()
    executor = CandidateRelevanceRunExecutor(
        runs=cast(SearchRunRepository, object()),
        search_run_id=run.id,
        session_store=cast(SearchSessionStore, store),
        citation_enrichment_limit=0,
        citation_enricher=None,
        attempt_no=1,
        relevance_queue=queue,
    )
    workflow = FakeWorkflowService()
    executor._workflow_service = cast(SearchRunService, workflow)

    queued = await executor._retry_technical_failure(
        run=run,
        session_key=run.redis_session_key or "",
        failure=CandidateRelevanceTechnicalFailure(
            "candidate_relevance_model_unavailable",
            "模型暂时不可用。",
        ),
    )

    assert queued is True
    assert queue.calls == [(run.id, 2)]
    assert store.snapshot["relevance_attempt_no"] == 2
    assert store.snapshot["stage"] == SearchRunStage.RELEVANCE_ASSESSMENT.value
    assert workflow.progress_updates[0]["stage"] is SearchRunStage.RELEVANCE_ASSESSMENT


@pytest.mark.asyncio
async def test_stale_relevance_attempt_is_ignored_before_model_work() -> None:
    """旧任务序号不能覆盖已经排队的后续完整集合尝试。"""
    run = _run()
    store = FakeRelevanceStore(_snapshot(_candidate(), attempt_no=2))
    executor = CandidateRelevanceRunExecutor(
        runs=cast(SearchRunRepository, FakeSearchRunRepository(run)),
        search_run_id=run.id,
        session_store=cast(SearchSessionStore, store),
        citation_enrichment_limit=0,
        citation_enricher=None,
        attempt_no=1,
    )

    result = await executor.execute(arq_context={"job_id": "relevance-stale"})

    assert result == {"search_run_id": str(run.id), "status": "stale_attempt"}
    assert store.released_locks


def test_second_technical_failure_excludes_only_pending_candidates() -> None:
    """第二次完整集合失败后，未形成结论的项安全排除，已有结果保持不变。"""
    pending = _candidate()
    completed = _with_level(_candidate(), CandidateRelevanceLevel.CORE)

    excluded = CandidateRelevanceRunExecutor._exclude_pending_candidates(
        (pending, completed),
        code="candidate_relevance_output_invalid",
        message="候选相关性无法形成可靠结论。",
    )

    assert excluded[0].relevance_state is CandidateRelevanceState.EXCLUDED
    assert excluded[0].relevance_assessment is None
    assert excluded[1] == completed


@pytest.mark.asyncio
async def test_citation_enrichment_includes_background_candidates() -> None:
    """通过核验的背景参考与核心候选同样进入题录预取范围。"""
    calls: list[UUID] = []

    class CapturingCitationEnricher:
        async def enrich(self, candidate: UnifiedCandidate) -> UnifiedCandidate:
            calls.append(candidate.candidate_id)
            return candidate

    core = _with_level(_candidate(), CandidateRelevanceLevel.CORE)
    background = _with_level(_candidate(), CandidateRelevanceLevel.BACKGROUND)
    excluded = _with_level(_candidate(), CandidateRelevanceLevel.NOT_RECOMMENDED).model_copy(
        update={"relevance_state": CandidateRelevanceState.EXCLUDED}
    )
    executor = CandidateRelevanceRunExecutor(
        runs=cast(SearchRunRepository, object()),
        search_run_id=_run().id,
        session_store=cast(SearchSessionStore, object()),
        citation_enrichment_limit=2,
        citation_enricher=cast(CitationMetadataEnricher, CapturingCitationEnricher()),
    )

    await executor._enrich_citations((core, background, excluded))

    assert calls == [core.candidate_id, background.candidate_id]
