"""候选正式引用服务的会话边界与题录状态测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from app.modules.documents.contracts import (
    CandidateFulltextState,
    FulltextAcquisitionError,
    FulltextAcquisitionErrorCode,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
)
from app.modules.literature.api_contracts import (
    CandidateCitationError,
    CandidateCitationErrorCode,
)
from app.modules.literature.citation_formatter import CitationFormat
from app.modules.literature.contracts import (
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
)
from app.modules.search.citation_service import CandidateCitationService
from app.modules.search.contracts import (
    CandidateAuthor,
    CandidateLinks,
    RawCandidate,
    SourceName,
    UnifiedCandidate,
)
from app.modules.search.fulltext_candidate import to_fulltext_candidate
from app.modules.search.run_models import SearchRunRecord
from app.modules.search.run_repository import SearchRunRepository
from tests.unit.fakes_search_candidates import FakeSearchCandidateRepository

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000901")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000902")
_PLAN_ID = UUID("00000000-0000-0000-0000-000000000903")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000904")
_CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000905")
_SESSION_KEY = "academic-search:search-run:00000000-0000-0000-0000-000000000904"


class FakeSearchRunRepository:
    """只模拟候选读取所依赖的检索运行所有权查询。"""

    def __init__(self, run: SearchRunRecord) -> None:
        self._run = run

    async def get_owned_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        search_run_id: UUID,
        for_update: bool = False,
    ) -> SearchRunRecord | None:
        del for_update
        if (
            owner_user_id != _OWNER_ID
            or collection_id != self._run.collection_id
            or search_run_id != self._run.id
        ):
            return None
        return self._run


def _run() -> SearchRunRecord:
    """构造完成检索运行。"""
    now = datetime.now(UTC)
    return SearchRunRecord(
        id=_RUN_ID,
        collection_id=_COLLECTION_ID,
        research_plan_id=_PLAN_ID,
        arq_job_id=None,
        redis_session_key=_SESSION_KEY,
        status="completed",
        stage="completed",
        attempt_no=1,
        provider_summary={},
        candidate_counts={},
        error_code=None,
        error_message=None,
        started_at=now,
        finished_at=now,
        created_at=now,
        updated_at=now,
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


def _candidate(citation: CitationMetadata | None) -> UnifiedCandidate:
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


def _fulltext_state(citation: CitationMetadata) -> CandidateFulltextState:
    """模拟全文任务已补齐题录，但尚未回写主候选投影的中间状态。"""
    return CandidateFulltextState(
        search_run_id=_RUN_ID,
        candidate=to_fulltext_candidate(_candidate(citation)),
        attempt_no=1,
        result=FulltextAcquisitionResult(
            candidate_id=_CANDIDATE_ID,
            status=FulltextAcquisitionStatus.FAILED,
            error=FulltextAcquisitionError(
                code=FulltextAcquisitionErrorCode.REMOTE_ERROR,
                message="全文来源返回 HTTP 403。",
                retryable=False,
                http_status_code=403,
            ),
        ),
        requested_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _candidate_repository(
    candidate: UnifiedCandidate,
    *,
    fulltext_states: tuple[CandidateFulltextState, ...] = (),
) -> FakeSearchCandidateRepository:
    """初始化服务端持久候选事实。"""
    return FakeSearchCandidateRepository(
        search_run_id=_RUN_ID,
        candidates=(candidate,),
        fulltext_states=fulltext_states,
    )


@pytest.mark.asyncio
async def test_render_uses_the_server_side_ready_metadata_for_the_requested_style() -> None:
    """前端仅传递格式枚举，引用文本必须由会话内 `ready` 题录生成。"""
    candidate = _candidate(_citation())
    service = CandidateCitationService(
        cast(SearchRunRepository, FakeSearchRunRepository(_run())),
        _candidate_repository(candidate),
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
        cast(SearchRunRepository, FakeSearchRunRepository(_run())),
        _candidate_repository(candidate),
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


@pytest.mark.asyncio
async def test_render_uses_ready_metadata_from_the_candidate_fulltext_state() -> None:
    """全文 Worker 补齐题录后，详情页和引用接口必须读取同一份服务端事实。"""
    candidate = _candidate(None)
    state = _fulltext_state(_citation())
    service = CandidateCitationService(
        cast(SearchRunRepository, FakeSearchRunRepository(_run())),
        _candidate_repository(candidate, fulltext_states=(state,)),
    )

    rendered = await service.render(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_id=_CANDIDATE_ID,
        citation_format=CitationFormat.GB_T_7714_2015_NUMERIC,
    )

    assert rendered.candidate_id == _CANDIDATE_ID
    assert "evidence for sleep and student wellbeing" in rendered.text.casefold()
