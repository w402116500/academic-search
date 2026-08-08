"""SQLAlchemy persistence for search-run candidate review facts."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.workflow import (
    SearchCandidateFulltextState as DbSearchCandidateFulltextState,
)
from app.infra.db.models.workflow import SearchRunCandidate
from app.modules.documents.contracts import (
    AcquiredFulltext,
    CandidateFulltextState,
    FulltextAcquisitionError,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
    FulltextCandidate,
)
from app.modules.search.contracts import (
    CandidateLanguage,
    CandidateLinks,
    RawCandidate,
    SourceName,
    UnifiedCandidate,
)


class SqlAlchemySearchCandidateRepository:
    """Persist candidate review facts without promoting them to Paper records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_candidates(
        self,
        *,
        search_run_id: UUID,
        candidates: Sequence[UnifiedCandidate],
    ) -> None:
        if not candidates:
            await self._delete_run_candidates(search_run_id)
            return

        rows = [
            _candidate_row(search_run_id=search_run_id, position=position, candidate=candidate)
            for position, candidate in enumerate(candidates)
        ]
        statement = pg_insert(SearchRunCandidate).values(rows)
        excluded = statement.excluded
        await self._session.execute(
            statement.on_conflict_do_update(
                constraint="search_run_candidate_identity",
                set_={
                    "position": excluded.position,
                    "doi": excluded.doi,
                    "title": excluded.title,
                    "title_key": excluded.title_key,
                    "language": excluded.language,
                    "authors": excluded.authors,
                    "abstract": excluded.abstract,
                    "published_year": excluded.published_year,
                    "published_date": excluded.published_date,
                    "venue": excluded.venue,
                    "document_type": excluded.document_type,
                    "volume": excluded.volume,
                    "issue": excluded.issue,
                    "pages": excluded.pages,
                    "article_number": excluded.article_number,
                    "publisher": excluded.publisher,
                    "citation_counts_by_source": excluded.citation_counts_by_source,
                    "links": excluded.links,
                    "is_open_access": excluded.is_open_access,
                    "source_refs": excluded.source_refs,
                    "triage": excluded.triage,
                    "relevance_state": excluded.relevance_state,
                    "relevance_assessment": excluded.relevance_assessment,
                    "relevance_error": excluded.relevance_error,
                    "citation": excluded.citation,
                    "pdf_availability": excluded.pdf_availability,
                    "relevance_retry_attempt_no": None,
                },
            )
        )
        await self._session.commit()

    async def list_candidates(self, *, search_run_id: UUID) -> tuple[UnifiedCandidate, ...]:
        rows = (
            await self._session.scalars(
                select(SearchRunCandidate)
                .where(SearchRunCandidate.search_run_id == search_run_id)
                .order_by(SearchRunCandidate.position.asc(), SearchRunCandidate.candidate_id.asc())
            )
        ).all()
        return tuple(_candidate_from_model(row) for row in rows)

    async def get_candidate(
        self,
        *,
        search_run_id: UUID,
        candidate_id: UUID,
    ) -> UnifiedCandidate | None:
        row = await self._session.get(SearchRunCandidate, (search_run_id, candidate_id))
        return _candidate_from_model(row) if row is not None else None

    async def selected_ids(self, *, search_run_id: UUID) -> set[UUID]:
        rows = await self._session.scalars(
            select(SearchRunCandidate.candidate_id).where(
                SearchRunCandidate.search_run_id == search_run_id,
                SearchRunCandidate.selected_at.is_not(None),
            )
        )
        return set(rows.all())

    async def set_selected(
        self,
        *,
        search_run_id: UUID,
        candidate_ids: Sequence[UUID],
        selected: bool,
    ) -> int:
        if candidate_ids:
            await self._session.execute(
                update(SearchRunCandidate)
                .where(
                    SearchRunCandidate.search_run_id == search_run_id,
                    SearchRunCandidate.candidate_id.in_(tuple(candidate_ids)),
                )
                .values(selected_at=datetime.now(UTC) if selected else None)
            )
            await self._session.commit()
        return len(await self.selected_ids(search_run_id=search_run_id))

    async def clear_selection(self, *, search_run_id: UUID) -> None:
        await self._session.execute(
            update(SearchRunCandidate)
            .where(SearchRunCandidate.search_run_id == search_run_id)
            .values(selected_at=None)
        )
        await self._session.commit()

    async def prune_selection(
        self,
        *,
        search_run_id: UUID,
        allowed_candidate_ids: set[UUID],
    ) -> None:
        statement = update(SearchRunCandidate).where(
            SearchRunCandidate.search_run_id == search_run_id,
            SearchRunCandidate.selected_at.is_not(None),
        )
        if allowed_candidate_ids:
            statement = statement.where(
                SearchRunCandidate.candidate_id.not_in(allowed_candidate_ids)
            )
        await self._session.execute(statement.values(selected_at=None))
        await self._session.commit()

    async def get_fulltext_state(
        self,
        *,
        search_run_id: UUID,
        candidate_id: UUID,
    ) -> CandidateFulltextState | None:
        row = await self._session.get(
            DbSearchCandidateFulltextState,
            (search_run_id, candidate_id),
        )
        return _fulltext_state_from_model(row) if row is not None else None

    async def list_fulltext_states(
        self,
        *,
        search_run_id: UUID,
        candidate_ids: Sequence[UUID],
    ) -> dict[UUID, CandidateFulltextState]:
        if not candidate_ids:
            return {}
        rows = (
            await self._session.scalars(
                select(DbSearchCandidateFulltextState).where(
                    DbSearchCandidateFulltextState.search_run_id == search_run_id,
                    DbSearchCandidateFulltextState.candidate_id.in_(tuple(candidate_ids)),
                )
            )
        ).all()
        return {row.candidate_id: _fulltext_state_from_model(row) for row in rows}

    async def write_fulltext_state(self, state: CandidateFulltextState) -> None:
        statement = pg_insert(DbSearchCandidateFulltextState).values(_fulltext_state_row(state))
        excluded = statement.excluded
        await self._session.execute(
            statement.on_conflict_do_update(
                constraint="search_candidate_fulltext_state_identity",
                set_={
                    "attempt_no": excluded.attempt_no,
                    "status": excluded.status,
                    "candidate": excluded.candidate,
                    "result_document": excluded.result_document,
                    "result_error": excluded.result_error,
                    "arq_job_id": excluded.arq_job_id,
                    "requested_at": excluded.requested_at,
                    "state_updated_at": excluded.state_updated_at,
                },
            )
        )
        await self._session.commit()

    async def update_relevance(
        self,
        *,
        search_run_id: UUID,
        candidates: Sequence[UnifiedCandidate],
    ) -> None:
        for candidate in candidates:
            await self._session.execute(
                update(SearchRunCandidate)
                .where(
                    SearchRunCandidate.search_run_id == search_run_id,
                    SearchRunCandidate.candidate_id == candidate.candidate_id,
                )
                .values(_relevance_values(candidate))
            )
        await self._session.commit()

    async def update_relevance_and_schedule_retry(
        self,
        *,
        search_run_id: UUID,
        resolved_candidates: Sequence[UnifiedCandidate],
        retry_attempt_no: int,
        retry_candidate_ids: Sequence[UUID],
    ) -> None:
        if retry_attempt_no < 1:
            raise ValueError("相关性重试序号必须从 1 开始。")
        for candidate in resolved_candidates:
            await self._session.execute(
                update(SearchRunCandidate)
                .where(
                    SearchRunCandidate.search_run_id == search_run_id,
                    SearchRunCandidate.candidate_id == candidate.candidate_id,
                )
                .values(_relevance_values(candidate))
            )
        await self._session.execute(
            update(SearchRunCandidate)
            .where(SearchRunCandidate.search_run_id == search_run_id)
            .values(relevance_retry_attempt_no=None)
        )
        if retry_candidate_ids:
            await self._session.execute(
                update(SearchRunCandidate)
                .where(
                    SearchRunCandidate.search_run_id == search_run_id,
                    SearchRunCandidate.candidate_id.in_(tuple(retry_candidate_ids)),
                )
                .values(relevance_retry_attempt_no=retry_attempt_no)
            )
        await self._session.commit()

    async def current_relevance_attempt_no(self, *, search_run_id: UUID) -> int:
        attempt_no = await self._session.scalar(
            select(func.max(SearchRunCandidate.relevance_retry_attempt_no)).where(
                SearchRunCandidate.search_run_id == search_run_id
            )
        )
        return int(attempt_no) if attempt_no is not None else 1

    async def relevance_retry_candidate_ids(
        self,
        *,
        search_run_id: UUID,
        attempt_no: int,
    ) -> frozenset[UUID] | None:
        rows = await self._session.scalars(
            select(SearchRunCandidate.candidate_id).where(
                SearchRunCandidate.search_run_id == search_run_id,
                SearchRunCandidate.relevance_retry_attempt_no == attempt_no,
            )
        )
        candidate_ids = frozenset(rows.all())
        return candidate_ids or None

    async def clear_relevance_retry(self, *, search_run_id: UUID) -> None:
        await self._session.execute(
            update(SearchRunCandidate)
            .where(SearchRunCandidate.search_run_id == search_run_id)
            .values(relevance_retry_attempt_no=None)
        )
        await self._session.commit()

    async def update_readiness(
        self,
        *,
        search_run_id: UUID,
        candidates: Sequence[UnifiedCandidate],
    ) -> None:
        for candidate in candidates:
            await self._session.execute(
                update(SearchRunCandidate)
                .where(
                    SearchRunCandidate.search_run_id == search_run_id,
                    SearchRunCandidate.candidate_id == candidate.candidate_id,
                )
                .values(
                    citation=_dump_model(candidate.citation),
                    pdf_availability=_dump_model(candidate.pdf_availability),
                )
            )
        await self._session.commit()

    async def _delete_run_candidates(self, search_run_id: UUID) -> None:
        await self._session.execute(
            delete(SearchRunCandidate).where(SearchRunCandidate.search_run_id == search_run_id)
        )
        await self._session.commit()


def _candidate_row(
    *,
    search_run_id: UUID,
    position: int,
    candidate: UnifiedCandidate,
) -> dict[str, Any]:
    return {
        "search_run_id": search_run_id,
        "candidate_id": candidate.candidate_id,
        "position": position,
        "doi": candidate.doi,
        "title": candidate.title,
        "title_key": candidate.title_key,
        "language": candidate.language.value,
        "authors": [author.model_dump(mode="json") for author in candidate.authors],
        "abstract": candidate.abstract,
        "published_year": candidate.published_year,
        "published_date": _dump_model(candidate.published_date),
        "venue": candidate.venue,
        "document_type": candidate.document_type,
        "volume": candidate.volume,
        "issue": candidate.issue,
        "pages": candidate.pages,
        "article_number": candidate.article_number,
        "publisher": candidate.publisher,
        "citation_counts_by_source": dict(candidate.citation_counts_by_source),
        "links": candidate.links.model_dump(mode="json"),
        "is_open_access": candidate.is_open_access,
        "source_refs": _source_refs(candidate),
        "triage": _dump_model(candidate.triage),
        "relevance_state": candidate.relevance_state.value,
        "relevance_assessment": _dump_model(candidate.relevance_assessment),
        "relevance_error": _dump_model(candidate.relevance_error),
        "citation": _dump_model(candidate.citation),
        "pdf_availability": _dump_model(candidate.pdf_availability),
        "relevance_retry_attempt_no": None,
    }


def _candidate_from_model(row: SearchRunCandidate) -> UnifiedCandidate:
    links = CandidateLinks.model_validate(row.links)
    payload = {
        "candidate_id": row.candidate_id,
        "doi": row.doi,
        "title": row.title,
        "title_key": row.title_key,
        "language": CandidateLanguage(row.language),
        "authors": row.authors,
        "abstract": row.abstract,
        "published_year": row.published_year,
        "published_date": row.published_date,
        "venue": row.venue,
        "document_type": row.document_type,
        "volume": row.volume,
        "issue": row.issue,
        "pages": row.pages,
        "article_number": row.article_number,
        "publisher": row.publisher,
        "citation_counts_by_source": row.citation_counts_by_source,
        "links": links.model_dump(mode="json"),
        "is_open_access": row.is_open_access,
        "source_records": [
            _source_record_from_ref(row, links=links, ref=ref) for ref in row.source_refs
        ],
        "field_provenance": {},
        "conflicts": {},
        "triage": row.triage,
        "relevance_state": row.relevance_state,
        "relevance_assessment": row.relevance_assessment,
        "relevance_error": row.relevance_error,
        "citation": row.citation,
        "pdf_availability": row.pdf_availability,
    }
    return UnifiedCandidate.model_validate(payload)


def _source_refs(candidate: UnifiedCandidate) -> list[dict[str, object]]:
    refs: list[dict[str, object]] = []
    for source_record in candidate.source_records:
        ref: dict[str, object] = {
            "source": source_record.source.value,
            "source_record_id": source_record.source_record_id,
        }
        if source_record.source_record_url is not None:
            ref["source_record_url"] = source_record.source_record_url
        if source_record.citation_count is not None:
            ref["citation_count"] = source_record.citation_count
        refs.append(ref)
    return refs


def _source_record_from_ref(
    row: SearchRunCandidate,
    *,
    links: CandidateLinks,
    ref: dict[str, object],
) -> dict[str, object | None]:
    source = SourceName(str(ref["source"]))
    source_record_id = str(ref["source_record_id"])
    source_record_url = ref.get("source_record_url")
    return RawCandidate.model_validate(
        {
            "source": source,
            "source_record_id": source_record_id,
            "source_record_url": str(source_record_url) if source_record_url is not None else None,
            "title": row.title,
            "language": CandidateLanguage(row.language),
            "authors": row.authors,
            "abstract": row.abstract,
            "published_year": row.published_year,
            "published_date": row.published_date,
            "doi": row.doi,
            "venue": row.venue,
            "document_type": row.document_type,
            "volume": row.volume,
            "issue": row.issue,
            "pages": row.pages,
            "article_number": row.article_number,
            "publisher": row.publisher,
            "citation_count": _source_ref_citation_count(ref.get("citation_count")),
            "landing_url": links.landing_url,
            "open_access_url": links.open_access_url,
            "fulltext_url": links.fulltext_url,
            "is_open_access": row.is_open_access,
        }
    ).model_dump(mode="json")


def _source_ref_citation_count(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("source_refs.citation_count 必须是整数或整数字符串。")


def _fulltext_state_row(state: CandidateFulltextState) -> dict[str, Any]:
    return {
        "search_run_id": state.search_run_id,
        "candidate_id": state.candidate.candidate_id,
        "attempt_no": state.attempt_no,
        "status": state.result.status.value,
        "candidate": state.candidate.model_dump(mode="json"),
        "result_document": _dump_model(state.result.document),
        "result_error": _dump_model(state.result.error),
        "arq_job_id": state.arq_job_id,
        "requested_at": state.requested_at,
        "state_updated_at": state.updated_at,
    }


def _fulltext_state_from_model(
    row: DbSearchCandidateFulltextState,
) -> CandidateFulltextState:
    result = FulltextAcquisitionResult(
        candidate_id=row.candidate_id,
        status=FulltextAcquisitionStatus(row.status),
        document=AcquiredFulltext.model_validate(row.result_document)
        if row.result_document is not None
        else None,
        error=FulltextAcquisitionError.model_validate(row.result_error)
        if row.result_error is not None
        else None,
    )
    return CandidateFulltextState(
        search_run_id=row.search_run_id,
        candidate=FulltextCandidate.model_validate(row.candidate),
        attempt_no=row.attempt_no,
        result=result,
        arq_job_id=row.arq_job_id,
        requested_at=row.requested_at,
        updated_at=row.state_updated_at,
    )


def _relevance_values(candidate: UnifiedCandidate) -> dict[str, Any]:
    return {
        "relevance_state": candidate.relevance_state.value,
        "relevance_assessment": _dump_model(candidate.relevance_assessment),
        "relevance_error": _dump_model(candidate.relevance_error),
    }


def _dump_model(value: Any | None) -> Any | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value
