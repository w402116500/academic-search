"""后端测试共享夹具。"""

from __future__ import annotations

import pytest
from app.core.settings import LiteratureSourceSettings


@pytest.fixture(autouse=True)
def isolate_literature_source_environment(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """防止本地 .env 的真实文献源配置污染离线单元测试。"""
    if request.node.get_closest_marker("live") is not None:
        return

    for field_name in LiteratureSourceSettings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)
