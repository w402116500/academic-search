"""工作区切换器所依赖的 OpenAPI 契约测试。"""

from __future__ import annotations

from app.api.routers.collections import _workspace_error_response
from app.main import app
from app.modules.collections.workspace_contracts import WorkspaceError, WorkspaceErrorCode


def test_workspace_list_openapi_exposes_search_and_cursor_pagination() -> None:
    """防止前端接入后列表接口退回一次性数组或丢失分页参数。"""
    operation = app.openapi()["paths"]["/api/v1/collections"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert set(parameters) >= {"include_archived", "q", "cursor", "limit"}
    assert parameters["limit"]["schema"]["default"] == 20
    assert parameters["limit"]["schema"]["maximum"] == 50
    assert response_schema == {"$ref": "#/components/schemas/WorkspaceListResponse"}


def test_damaged_workspace_cursor_maps_to_http_422() -> None:
    """无效游标是客户端可修复输入错误，不是工作区冲突。"""
    response = _workspace_error_response(
        WorkspaceError(WorkspaceErrorCode.INVALID_CURSOR, "invalid cursor")
    )

    assert response.status_code == 422
