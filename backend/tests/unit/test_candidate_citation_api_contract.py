"""候选正式引用端点的 OpenAPI 契约测试。"""

from __future__ import annotations

from app.main import app


def test_candidate_citation_endpoint_exposes_only_a_format_selector() -> None:
    """调用方只能选择样式，不能提交或覆盖服务端候选题录。"""
    path = (
        "/api/v1/collections/{collection_id}/search-runs/"
        "{search_run_id}/candidates/{candidate_id}/citation"
    )
    operation = app.openapi()["paths"][path]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert set(parameters) == {"collection_id", "search_run_id", "candidate_id", "format"}
    assert parameters["format"]["schema"]["default"] == "gb_t_7714_2015_numeric"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CandidateCitationResponse"
    }
