"""检索与入库日预算错误的 API 映射测试。"""

from __future__ import annotations

from typing import cast

from app.api.routers.collection_documents import _build_error_response
from app.api.routers.search_runs import _search_error_response
from app.modules.research.build_contracts import CollectionBuildError, CollectionBuildErrorCode
from app.modules.search.api_contracts import SearchRunError, SearchRunErrorCode


def test_search_submission_quota_errors_map_to_http_429() -> None:
    """前端应能区分资源冲突和可稍后重试的资源治理拒绝。"""
    for code in (
        SearchRunErrorCode.USER_QUOTA_EXCEEDED,
        SearchRunErrorCode.GLOBAL_BUDGET_EXHAUSTED,
    ):
        response = _search_error_response(SearchRunError(code, "检索配额已用尽。"))
        detail = cast(dict[str, object], response.detail)
        assert response.status_code == 429
        assert detail["code"] == code


def test_collection_build_submission_quota_errors_map_to_http_429() -> None:
    """批量构建与单篇重试均以稳定的 429 契约报告日预算拒绝。"""
    for code in (
        CollectionBuildErrorCode.USER_QUOTA_EXCEEDED,
        CollectionBuildErrorCode.GLOBAL_BUDGET_EXHAUSTED,
    ):
        response = _build_error_response(CollectionBuildError(code, "入库配额已用尽。"))
        detail = cast(dict[str, object], response.detail)
        assert response.status_code == 429
        assert detail["code"] == code
