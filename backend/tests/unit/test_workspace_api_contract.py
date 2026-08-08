"""工作区切换器所依赖的 OpenAPI 契约测试。"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from app.api.deps.auth import get_current_user
from app.api.deps.services import get_workspace_deletion_service
from app.api.routers.collections import _workspace_error_response
from app.main import app
from app.modules.research.workspace_contracts import WorkspaceError, WorkspaceErrorCode

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000801")
_WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000802")


def test_workspace_list_openapi_exposes_search_and_cursor_pagination() -> None:
    """防止前端接入后列表接口退回一次性数组或丢失分页参数。"""
    operation = app.openapi()["paths"]["/api/v1/collections"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert set(parameters) >= {"include_archived", "q", "cursor", "limit"}
    assert parameters["limit"]["schema"]["default"] == 20
    assert parameters["limit"]["schema"]["maximum"] == 50
    assert response_schema == {"$ref": "#/components/schemas/WorkspaceListResponse"}


def test_workspace_delete_openapi_exposes_no_content_response() -> None:
    """永久删除不返回已删除数据，避免前端误用过期工作区详情。"""
    operation = app.openapi()["paths"]["/api/v1/collections/{collection_id}"]["delete"]

    assert set(operation["responses"]) >= {"204", "422"}
    assert "content" not in operation["responses"]["204"]


def test_damaged_workspace_cursor_maps_to_http_422() -> None:
    """无效游标是客户端可修复输入错误，不是工作区冲突。"""
    response = _workspace_error_response(
        WorkspaceError(WorkspaceErrorCode.INVALID_CURSOR, "invalid cursor")
    )

    assert response.status_code == 422


def test_workspace_deletion_errors_map_to_conflict_or_service_unavailable() -> None:
    """删除未收敛与外部清理失败必须保留可恢复的 HTTP 语义。"""
    in_progress = _workspace_error_response(
        WorkspaceError(WorkspaceErrorCode.DELETION_IN_PROGRESS, "still stopping")
    )
    cleanup_failed = _workspace_error_response(
        WorkspaceError(WorkspaceErrorCode.DELETION_CLEANUP_FAILED, "cleanup failed")
    )

    assert in_progress.status_code == 409
    assert cleanup_failed.status_code == 503


@pytest.mark.asyncio
async def test_workspace_delete_hides_unavailable_workspace_behind_http_404() -> None:
    """删除路由必须将所有权边界产生的不可用结果统一为 404。"""

    class UnavailableWorkspaceDeletionService:
        def __init__(self) -> None:
            self.calls: list[tuple[UUID, UUID]] = []

        async def delete(self, *, owner_user_id: UUID, collection_id: UUID) -> None:
            self.calls.append((owner_user_id, collection_id))
            raise WorkspaceError(WorkspaceErrorCode.NOT_FOUND, "研究工作区不存在。")

    service = UnavailableWorkspaceDeletionService()

    async def fake_current_user() -> object:
        return SimpleNamespace(id=_OWNER_ID)

    async def fake_workspace_deletion_service() -> UnavailableWorkspaceDeletionService:
        return service

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_workspace_deletion_service] = fake_workspace_deletion_service
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.delete(f"/api/v1/collections/{_WORKSPACE_ID}")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_workspace_deletion_service, None)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == WorkspaceErrorCode.NOT_FOUND
    assert service.calls == [(_OWNER_ID, _WORKSPACE_ID)]
