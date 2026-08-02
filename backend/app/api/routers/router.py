from app.api.routers import (
    auth,
    candidate_citations,
    candidate_fulltext,
    collection_documents,
    collections,
    research_conversations,
    research_plans,
    search_runs,
)
from fastapi import APIRouter

# 所有业务 API 统一位于版本化前缀下，健康检查仍保留在应用根路径供探针使用。
router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(collections.router)
router.include_router(research_plans.router)
router.include_router(search_runs.router)
router.include_router(candidate_citations.router)
router.include_router(candidate_fulltext.router)
router.include_router(collection_documents.router)
router.include_router(research_conversations.router)
