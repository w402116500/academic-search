"""全文状态路由的底层检索运行错误映射测试。"""

from __future__ import annotations

from app.api.routers.candidate_fulltext import _fulltext_error_response, _search_run_error_response
from app.main import app
from app.modules.workflow.contracts import (
    CandidateFulltextError,
    CandidateFulltextErrorCode,
    SearchRunError,
    SearchRunErrorCode,
)


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


def test_upload_requires_explicit_authorization_instead_of_returning_a_server_error() -> None:
    """缺少授权声明属于可修正输入，不应被 API 隐藏为 500 或资源不存在。"""
    response = _fulltext_error_response(
        CandidateFulltextError(
            CandidateFulltextErrorCode.UPLOAD_NOT_AUTHORIZED,
            "请先确认你有权处理该 PDF。",
        )
    )

    assert response.status_code == 422


def test_authorized_pdf_upload_only_accepts_resource_identifiers_and_header_consent() -> None:
    """上传端点不能让客户端提交 DOI、URL、对象键或可伪造的文献题录。"""
    path = (
        "/api/v1/collections/{collection_id}/search-runs/"
        "{search_run_id}/candidates/{candidate_id}/fulltext/upload"
    )
    operation = app.openapi()["paths"][path]["post"]
    parameters = {parameter["name"] for parameter in operation["parameters"]}

    assert parameters == {
        "collection_id",
        "search_run_id",
        "candidate_id",
        "x-upload-authorized",
    }
    assert "requestBody" not in operation
