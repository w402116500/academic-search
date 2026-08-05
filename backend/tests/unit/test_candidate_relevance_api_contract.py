"""候选相关性不暴露用户控制端点的 OpenAPI 契约测试。"""

from __future__ import annotations

from app.main import app


def test_relevance_control_endpoints_are_not_public() -> None:
    """技术恢复仅在 Worker 内执行，任何相关性重试/取消路由都不能重新出现。"""
    paths = app.openapi()["paths"]
    relevance_paths = [path for path in paths if "/relevance/" in path]

    assert relevance_paths == []
