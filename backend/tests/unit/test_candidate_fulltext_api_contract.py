"""全文状态路由的底层检索运行错误映射测试。"""

from __future__ import annotations

from app.api.routers.candidate_fulltext import _search_run_error_response
from app.modules.workflow.contracts import SearchRunError, SearchRunErrorCode


def test_foreign_search_run_is_hidden_by_the_fulltext_status_route() -> None:
    """全文轮询不能因底层所有权查询异常而变成 HTTP 500。"""
    response = _search_run_error_response(
        SearchRunError(SearchRunErrorCode.RUN_NOT_FOUND, "检索运行不存在。")
    )

    assert response.status_code == 404


def test_fulltext_route_marks_search_queue_unavailable_as_retryable_server_error() -> None:
    """队列故障与资源不存在不同，前端可据 503 提示稍后重试。"""
    response = _search_run_error_response(
        SearchRunError(SearchRunErrorCode.QUEUE_UNAVAILABLE, "检索队列不可用。")
    )

    assert response.status_code == 503
