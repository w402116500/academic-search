"""候选正式引用服务的会话边界与题录状态测试。"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models.workflow import SearchRun
from app.modules.search.citation_formatter import CitationFormat
from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateLinks,
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
    RawCandidate,
    SourceName,
    UnifiedCandidate,
)
from app.modules.workflow.citation_service import CandidateCitationService
from app.modules.workflow.contracts import CandidateCitationError, CandidateCitationErrorCode
from app.modules.workflow.search_session import SearchSessionStore
from sqlalchemy.ext.asyncio import AsyncSession

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000901")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000902")
_PLAN_ID = UUID("00000000-0000-0000-0000-000000000903")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000904")
_CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000905")
_SESSION_KEY = "academic-search:search-run:00000000-0000-0000-0000-000000000904"


class FakeSession:
    """只模拟候选读取所依赖的检索运行所有权查询。"""

    def __init__(self, run: SearchRun) -> None:
        self._run = run

    async def scalar(self, _statement: object) -> SearchRun:
        return self._run


class FakeSessionStore:
    """用内存快照替代 Redis，保持候选只能从服务端会话读取。"""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self._snapshot = snapshot

    async def read_snapshot(self, session_key: str) -> dict[str, Any] | None:
        return self._snapshot if session_key == _SESSION_KEY else None


def _run() -> SearchRun:
    """构造拥有短期候选快照的完成检索运行。"""
    return SearchRun(
        id=_RUN_ID,
        collection_id=_COLLECTION_ID,
        research_plan_id=_PLAN_ID,
        redis_session_key=_SESSION_KEY,
        status="completed",
        stage="completed",
        attempt_no=1,
        provider_summary={},
        candidate_counts={},
    )


def _citation(*, status: CitationMetadataStatus = CitationMetadataStatus.READY) -> CitationMetadata:
    """构造可用于 CSL 与 BibTeX 的同一份规范化题录。"""
    metadata = CitationMetadata(
        status=CitationMetadataStatus.READY,
        authors=(CitationAuthor(given="Ada", family="Lovelace"),),
        title="Evidence for sleep and student wellbeing",
        document_type="journal_article",
        issued_date=CitationDate(year=2024, month=5, day=1),
        venue="Journal of Student Health",
        volume="12",
        issue="3",
        pages="101-115",
        doi="10.1000/citation.example",
        url="https://doi.org/10.1000/citation.example",
    )
    if status is CitationMetadataStatus.READY:
        return metadata
    return metadata.model_copy(update={"status": status, "missing_fields": ("volume",)})


def _candidate(citation: CitationMetadata) -> UnifiedCandidate:
    """构造带有服务端题录的候选，不从调用方输入接收书目信息。"""
    source_record = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id="W-citation-test",
        title="Evidence for sleep and student wellbeing",
        authors=(CandidateAuthor(name="Ada Lovelace"),),
        doi="10.1000/citation.example",
    )
    return UnifiedCandidate(
        candidate_id=_CANDIDATE_ID,
        doi="10.1000/citation.example",
        title=source_record.title,
        title_key="evidence for sleep and student wellbeing",
        authors=source_record.authors,
        links=CandidateLinks(landing_url="https://doi.org/10.1000/citation.example"),
        source_records=(source_record,),
        citation=citation,
    )


@pytest.mark.asyncio
async def test_render_uses_the_server_side_ready_metadata_for_the_requested_style() -> None:
    """前端仅传递格式枚举，引用文本必须由会话内 `ready` 题录生成。"""
    candidate = _candidate(_citation())
    service = CandidateCitationService(
        cast(AsyncSession, FakeSession(_run())),
        cast(
            SearchSessionStore,
            FakeSessionStore({"candidates": [candidate.model_dump(mode="json")]}),
        ),
    )

    rendered = await service.render(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_id=_CANDIDATE_ID,
        citation_format=CitationFormat.APA_7,
    )

    assert rendered.candidate_id == _CANDIDATE_ID
    assert rendered.format is CitationFormat.APA_7
    assert "evidence for sleep and student wellbeing" in rendered.text.casefold()
    assert "10.1000/citation.example" in rendered.text


@pytest.mark.asyncio
async def test_render_rejects_incomplete_metadata_instead_of_returning_a_fake_citation() -> None:
    """不完整、冲突或解析失败的题录必须被拒绝，而不是降级为手工拼接文本。"""
    candidate = _candidate(_citation(status=CitationMetadataStatus.PARTIAL))
    service = CandidateCitationService(
        cast(AsyncSession, FakeSession(_run())),
        cast(
            SearchSessionStore,
            FakeSessionStore({"candidates": [candidate.model_dump(mode="json")]}),
        ),
    )

    with pytest.raises(CandidateCitationError) as raised:
        await service.render(
            owner_user_id=_OWNER_ID,
            collection_id=_COLLECTION_ID,
            search_run_id=_RUN_ID,
            candidate_id=_CANDIDATE_ID,
            citation_format=CitationFormat.GB_T_7714_2015_NUMERIC,
        )

    assert raised.value.code is CandidateCitationErrorCode.CITATION_NOT_READY
