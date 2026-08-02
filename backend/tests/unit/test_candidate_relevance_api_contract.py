"""候选相关性重试端点的 OpenAPI 契约测试。"""

from __future__ import annotations

from app.main import app


def test_candidate_relevance_retry_endpoint_accepts_only_path_identifiers() -> None:
    """前端只能请求重试既有候选，不能提交标题、摘要或 Agent 结论。"""
    path = (
        "/api/v1/collections/{collection_id}/search-runs/"
        "{search_run_id}/candidates/{candidate_id}/relevance/retry"
    )
    operation = app.openapi()["paths"][path]["post"]
    parameters = {parameter["name"] for parameter in operation["parameters"]}

    assert parameters == {"collection_id", "search_run_id", "candidate_id"}
    assert "requestBody" not in operation
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SearchCandidatesResponse"
    }
