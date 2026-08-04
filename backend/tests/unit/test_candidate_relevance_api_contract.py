"""候选相关性重试端点的 OpenAPI 契约测试。"""

from __future__ import annotations

from app.main import app


def test_relevance_run_control_endpoints_accept_only_run_identifiers() -> None:
    """重试和取消都作用于完整候选集合，不能回退到单候选串行接口。"""
    paths = app.openapi()["paths"]
    path = "/api/v1/collections/{collection_id}/search-runs/{search_run_id}/relevance/retry"
    operation = paths[path]["post"]
    parameters = {parameter["name"] for parameter in operation["parameters"]}

    assert parameters == {"collection_id", "search_run_id"}
    assert "requestBody" not in operation
    assert operation["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SearchCandidatesResponse"
    }
    assert (
        "/api/v1/collections/{collection_id}/search-runs/{search_run_id}/relevance/cancel"
    ) in paths
    assert (
        "/api/v1/collections/{collection_id}/search-runs/"
        "{search_run_id}/candidates/{candidate_id}/relevance/retry"
    ) not in paths
