"""研究 Worker 失败分类的离线契约测试。"""

from __future__ import annotations

from app.modules.rag.retrieval import ResearchRerankerError, RetrievalUnavailableError
from app.workers.research import _failure_from_unexpected_exception


def test_retrieval_unavailable_failure_has_safe_diagnostics() -> None:
    """检索前置条件失败时应保留可展示原因，并只写入安全诊断字段。"""
    failure = _failure_from_unexpected_exception(
        RetrievalUnavailableError("当前研究集合没有可检索的当前文档版本。")
    )

    assert failure.code == "research_no_researchable_documents"
    assert failure.message == "当前研究集合没有可检索的当前文档版本。"
    assert failure.diagnostics == {
        "component": "retrieval",
        "error_type": "RetrievalUnavailableError",
    }


def test_reranker_failure_has_component_diagnostics_without_raw_detail() -> None:
    """重排器异常若越过检索层，也不能只落成 generic 失败。"""
    failure = _failure_from_unexpected_exception(ResearchRerankerError("真实 Reranker 调用失败。"))

    assert failure.code == "research_reranker_failed"
    assert failure.message == "证据重排服务暂时不可用，请稍后重试。"
    assert failure.diagnostics == {
        "component": "reranker",
        "error_type": "ResearchRerankerError",
    }
