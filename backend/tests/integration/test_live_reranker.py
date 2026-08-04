"""真实 HTTP Reranker 的环境门控验收。"""

from __future__ import annotations

import math
import os
from uuid import UUID

import pytest
from app.modules.research.retrieval import HttpResearchReranker, RetrievedEvidence
from app.modules.research.settings import get_research_settings

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_RERANKER_TESTS"


def _evidence(index: int, content: str) -> RetrievedEvidence:
    """构造不写入任何基础设施的最小检索候选。"""
    suffix = f"{index + 1:012d}"
    return RetrievedEvidence(
        chunk_id=UUID(f"00000000-0000-0000-0000-{suffix}"),
        document_id=UUID("00000000-0000-0000-0000-000000000101"),
        ingestion_run_id=UUID("00000000-0000-0000-0000-000000000102"),
        paper_id=UUID("00000000-0000-0000-0000-000000000103"),
        content=content,
        page_start=1,
        page_end=1,
        section_path=("验收样本",),
        locator={"paragraph": index + 1},
        title="Reranker live validation fixture",
        authors=(),
        publication_year=2026,
        source_url=None,
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_reranker_returns_valid_semantic_order_for_chinese_evidence() -> None:
    """真实服务必须返回输入池内下标，并将直答片段排在无关候选之前。"""
    if os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) != "1":
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实 Reranker 验收")

    get_research_settings.cache_clear()
    settings = get_research_settings()
    assert settings.reranker_enabled

    evidences = (
        _evidence(
            0,
            "Transformer 的 self-attention 用于关联同一序列中的不同位置，"
            "从而计算输入或输出的表示。",
        ),
        _evidence(
            1,
            "Transformer 的 multi-head attention 允许模型在不同表示子空间中并行关注"
            "不同位置的信息，因此能够同时建模多种关系。",
        ),
        _evidence(
            2,
            "随机对照试验通过分配干预组和对照组来评估医疗干预的因果效应。",
        ),
    )
    matches = await HttpResearchReranker(settings).rerank(
        query="Transformer 中 multi-head attention 的作用是什么？",
        evidences=evidences,
        limit=len(evidences),
    )

    assert len(matches) == len(evidences)
    assert len({match.index for match in matches}) == len(matches)
    assert all(0 <= match.index < len(evidences) for match in matches)
    assert all(math.isfinite(match.score) for match in matches)
    assert [match.score for match in matches] == sorted(
        (match.score for match in matches), reverse=True
    )
    assert matches[0].index == 1
    assert matches[0].score > matches[-1].score
