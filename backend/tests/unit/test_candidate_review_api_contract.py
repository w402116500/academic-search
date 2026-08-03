"""候选审核分页与批量接口的 OpenAPI 契约测试。"""

from __future__ import annotations

from app.main import app


def test_candidate_review_endpoints_keep_selection_and_batch_inputs_server_owned() -> None:
    """批量核验和入集合只读取 Redis 准备清单，前端不能提交题录或全文字段。"""
    base_path = "/api/v1/collections/{collection_id}/search-runs/{search_run_id}"
    paths = app.openapi()["paths"]

    selection = paths[f"{base_path}/candidate-selection"]["patch"]
    clear_selection = paths[f"{base_path}/candidate-selection"]["delete"]
    prepare = paths[f"{base_path}/candidate-selection/prepare"]["post"]
    admission = paths[f"{base_path}/candidate-selection/admission"]["post"]
    candidates = paths[f"{base_path}/candidates"]["get"]
    candidate = paths[f"{base_path}/candidates/{{candidate_id}}"]["get"]

    assert "requestBody" in selection
    assert "requestBody" not in clear_selection
    assert "requestBody" not in prepare
    assert "requestBody" not in admission
    assert {parameter["name"] for parameter in candidates["parameters"]} == {
        "collection_id",
        "search_run_id",
        "limit",
        "cursor",
        "query",
        "filter",
    }
    assert {parameter["name"] for parameter in candidate["parameters"]} == {
        "collection_id",
        "search_run_id",
        "candidate_id",
    }
