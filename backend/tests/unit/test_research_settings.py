"""研究运行配置的空值与可选 Reranker 契约测试。"""

from __future__ import annotations

import pytest
from app.modules.research.settings import ResearchSettings


def test_blank_reranker_environment_variables_keep_reranking_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """待填写的 `.env` 空项不能让应用启动时误判为半配置。"""
    monkeypatch.setenv("RAG_RERANKER_URL", "")
    monkeypatch.setenv("RAG_RERANKER_API_KEY", "")
    monkeypatch.setenv("RAG_RERANKER_MODEL", "")

    settings = ResearchSettings()

    assert settings.rag_reranker_url is None
    assert settings.rag_reranker_api_key is None
    assert settings.rag_reranker_model is None
    assert settings.reranker_enabled is False
