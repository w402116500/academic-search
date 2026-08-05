"""Named request-scoped service and adapter composition for FastAPI routers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fulltext_settings import get_fulltext_acquisition_settings
from app.core.settings import get_literature_source_settings
from app.core.workflow_settings import get_workflow_settings
from app.infra.db.repositories.collection_builds import SqlAlchemyCollectionBuildAdapter
from app.infra.db.repositories.literature_admission import (
    SqlAlchemyLiteratureAdmissionAdapter,
)
from app.infra.db.repositories.research_conversations import (
    SqlAlchemyResearchConversationAdapter,
)
from app.infra.db.repositories.research_plans import SqlAlchemyResearchPlanRepository
from app.infra.db.repositories.search_runs import SqlAlchemySearchRunRepository
from app.infra.db.repositories.users import SqlAlchemyUserRepository
from app.infra.db.repositories.workspaces import SqlAlchemyWorkspaceRepository
from app.infra.db.session import get_db_session
from app.infra.redis.connection import redis_client_from_environment
from app.infra.redis.job_queues import (
    ArqCandidateFulltextJobQueue,
    ArqIngestionJobQueue,
    ArqResearchJobQueue,
    ArqResearchPlanJobQueue,
    ArqSearchRunJobQueue,
)
from app.infra.redis.research_events import RedisResearchEventStore
from app.infra.redis.search_session import RedisSearchSessionStore
from app.infra.storage.documents import Boto3StagingObjectStorage
from app.modules.auth.repository import UserRepository
from app.modules.auth.service import AuthenticationService
from app.modules.documents.acquisition import AuthorizedPdfUploader
from app.modules.documents.service import CandidateFulltextService
from app.modules.literature.admission import LiteratureAdmissionPort
from app.modules.research.collection_build import CollectionBuildUseCases
from app.modules.research.conversation import ResearchConversationUseCases
from app.modules.research.events import ResearchEventStore
from app.modules.research.plan_service import ResearchPlanService
from app.modules.research.settings import get_research_settings
from app.modules.research.workspace_service import ResearchWorkspaceService
from app.modules.search.citation_service import CandidateCitationService
from app.modules.search.fulltext_candidate import SearchCandidateFulltextLookup
from app.modules.search.review_admission import CandidateAdmissionService
from app.modules.search.review_preparation import CandidatePreparationService
from app.modules.search.review_query import CandidateReviewQueryService
from app.modules.search.review_selection import CandidateSelectionService
from app.modules.search.review_session import CandidateReviewSession
from app.modules.search.run_repository import SearchRunRepository
from app.modules.search.run_service import SearchRunService
from app.modules.search.session import SearchSessionStore


def get_user_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserRepository:
    return SqlAlchemyUserRepository(session)


def get_authentication_service(
    users: Annotated[UserRepository, Depends(get_user_repository)],
) -> AuthenticationService:
    return AuthenticationService(users)


def get_workspace_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResearchWorkspaceService:
    return ResearchWorkspaceService(SqlAlchemyWorkspaceRepository(session))


async def get_search_session_store() -> AsyncIterator[SearchSessionStore]:
    """Yield one Redis-backed search session adapter and close it after the response."""
    redis = redis_client_from_environment()
    settings = get_literature_source_settings()
    try:
        yield RedisSearchSessionStore(redis, ttl_seconds=settings.search_session_ttl_seconds)
    finally:
        await redis.aclose()


async def get_research_event_store() -> AsyncIterator[ResearchEventStore]:
    """Yield one Redis-backed research event adapter for an SSE response lifecycle."""
    redis = redis_client_from_environment()
    settings = get_research_settings()
    try:
        yield RedisResearchEventStore(redis, ttl_seconds=settings.rag_event_ttl_seconds)
    finally:
        await redis.aclose()


def get_research_plan_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResearchPlanService:
    return ResearchPlanService(
        SqlAlchemyResearchPlanRepository(session),
        ArqResearchPlanJobQueue(),
    )


def get_search_run_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SearchRunRepository:
    return SqlAlchemySearchRunRepository(session)


def get_search_run_service(
    runs: Annotated[SearchRunRepository, Depends(get_search_run_repository)],
) -> SearchRunService:
    return SearchRunService(
        runs,
        ArqSearchRunJobQueue(),
        settings=get_workflow_settings(),
    )


def get_collection_admission_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LiteratureAdmissionPort:
    return SqlAlchemyLiteratureAdmissionAdapter(
        session,
        Boto3StagingObjectStorage(get_fulltext_acquisition_settings()),
    )


def get_candidate_review_session(
    runs: Annotated[SearchRunRepository, Depends(get_search_run_repository)],
    store: Annotated[SearchSessionStore, Depends(get_search_session_store)],
) -> CandidateReviewSession:
    return CandidateReviewSession(runs, store)


def get_candidate_review_query_service(
    session: Annotated[CandidateReviewSession, Depends(get_candidate_review_session)],
) -> CandidateReviewQueryService:
    return CandidateReviewQueryService(session)


def get_candidate_selection_service(
    session: Annotated[CandidateReviewSession, Depends(get_candidate_review_session)],
) -> CandidateSelectionService:
    return CandidateSelectionService(session)


def get_candidate_fulltext_service(
    runs: Annotated[SearchRunRepository, Depends(get_search_run_repository)],
    store: Annotated[SearchSessionStore, Depends(get_search_session_store)],
) -> CandidateFulltextService:
    return CandidateFulltextService(
        SearchRunService(runs),
        store,
        ArqCandidateFulltextJobQueue(),
        candidate_lookup=SearchCandidateFulltextLookup(runs, store),
    )


def get_candidate_review_prepare_service(
    session: Annotated[CandidateReviewSession, Depends(get_candidate_review_session)],
    fulltext: Annotated[CandidateFulltextService, Depends(get_candidate_fulltext_service)],
) -> CandidatePreparationService:
    return CandidatePreparationService(
        session,
        fulltext,
    )


def get_candidate_review_admission_service(
    session: Annotated[CandidateReviewSession, Depends(get_candidate_review_session)],
    fulltext: Annotated[CandidateFulltextService, Depends(get_candidate_fulltext_service)],
    selection: Annotated[CandidateSelectionService, Depends(get_candidate_selection_service)],
    admission: Annotated[LiteratureAdmissionPort, Depends(get_collection_admission_service)],
) -> CandidateAdmissionService:
    return CandidateAdmissionService(
        session,
        fulltext,
        admission,
        selection,
    )


def get_candidate_upload_service(
    runs: Annotated[SearchRunRepository, Depends(get_search_run_repository)],
    store: Annotated[SearchSessionStore, Depends(get_search_session_store)],
) -> CandidateFulltextService:
    settings = get_fulltext_acquisition_settings()
    storage = Boto3StagingObjectStorage(settings)
    return CandidateFulltextService(
        SearchRunService(runs),
        store,
        ArqCandidateFulltextJobQueue(),
        candidate_lookup=SearchCandidateFulltextLookup(runs, store),
        uploader=AuthorizedPdfUploader(settings, storage),
    )


def get_candidate_citation_service(
    runs: Annotated[SearchRunRepository, Depends(get_search_run_repository)],
    store: Annotated[SearchSessionStore, Depends(get_search_session_store)],
) -> CandidateCitationService:
    return CandidateCitationService(runs, store)


def get_collection_build_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CollectionBuildUseCases:
    return SqlAlchemyCollectionBuildAdapter(session, ArqIngestionJobQueue())


def get_research_conversation_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResearchConversationUseCases:
    return SqlAlchemyResearchConversationAdapter(session, ArqResearchJobQueue())
