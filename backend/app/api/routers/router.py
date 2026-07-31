from app.api.routers import auth, collections, research_plans
from fastapi import APIRouter

# 所有业务 API 统一位于版本化前缀下，健康检查仍保留在应用根路径供探针使用。
router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(collections.router)
router.include_router(research_plans.router)
