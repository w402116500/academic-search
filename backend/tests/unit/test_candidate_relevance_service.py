"""候选相关性单项重试的会话、所有权与幂等行为测试。"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models.workflow import ResearchPlan, SearchRun
from app.modules.search.contracts import (
    CandidateRelevanceError,
    CandidateRelevanceState,
    RawCandidate,
    SourceName,
    TriageDecision,
    UnifiedCandidate,
)
from app.modules.workflow.candidate_relevance import OpenAICompatibleCandidateRelevanceEvaluator
from app.modules.workflow.candidate_relevance_service import CandidateRelevanceService
from app.modules.workflow.search_session import SearchSessionStore
from app.modules.workflow.settings import WorkflowSettings
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000701")
_COLLECTION_ID = UUID("00000000-0000-0000-0000-000000000702")
_PLAN_ID = UUID("00000000-0000-0000-0000-000000000703")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000704")
_CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000705")
_SESSION_KEY = "academic-search:search-run:00000000-0000-0000-0000-000000000704"


class FakeSession:
    """按调用顺序返回已授权搜索运行和其绑定计划。"""

    def __init__(self, run: SearchRun, plan: ResearchPlan) -> None:
        self._values = iter((run, plan))

    async def scalar(self, _statement: object) -> SearchRun | ResearchPlan:
        return next(self._values)


class FakeSessionStore:
    """在内存中模拟 Redis 快照和单项重试锁。"""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.writes: list[dict[str, Any]] = []
        self.locked = False

    async def read_snapshot(self, session_key: str) -> dict[str, Any] | None:
        return self.snapshot if session_key == _SESSION_KEY else None

    async def write_snapshot(self, session_key: str, snapshot: dict[str, Any]) -> None:
        assert session_key == _SESSION_KEY
        self.snapshot = snapshot
        self.writes.append(snapshot)

    async def try_acquire_lock(self, _key: str, *, token: str, ttl_seconds: int) -> bool:
        assert token
        assert ttl_seconds >= 180
        if self.locked:
            return False
        self.locked = True
        return True

    async def release_lock(self, _key: str, *, token: str) -> None:
        assert token
        self.locked = False


class FakeModel:
    """记录调用次数，并为当前候选返回可核验的结构化评估。"""

    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, input: list[SystemMessage | HumanMessage]) -> object:
        _ = input
        self.calls += 1
        return {
            "assessments": [
                {
                    "candidate_id": str(_CANDIDATE_ID),
                    "level": "core",
                    "study_focus": "考察睡眠质量与学生学业表现之间的关系。",
                    "reason": "研究对象和核心关系与当前已确认方向直接对应。",
                    "helpful_aspect": "可用于梳理睡眠和学业表现之间的关联证据。",
                    "limitations": ["判断范围仅限标题和摘要。"],
                    "recommendation": "建议优先查看正式题录和全文。",
                    "evidence": [
                        {
                            "source_field": "abstract",
                            "quote": "sleep quality and academic performance",
                        }
                    ],
                }
            ]
        }


def _run() -> SearchRun:
    """构造已经完成、仍保留 Redis 会话的检索运行。"""
    return SearchRun(
        id=_RUN_ID,
        collection_id=_COLLECTION_ID,
        research_plan_id=_PLAN_ID,
        redis_session_key=_SESSION_KEY,
        status="completed",
        stage="completed",
        attempt_no=1,
        provider_summary={},
        candidate_counts={},
    )


def _plan() -> ResearchPlan:
    """构造单项重试必须复用的已确认计划，不接收前端研究字段。"""
    return ResearchPlan(
        id=_PLAN_ID,
        collection_id=_COLLECTION_ID,
        revision=1,
        raw_request="睡眠质量是否影响大学生学业表现？",
        status="confirmed",
        selected_direction_id="sleep-performance",
        direction_options=[
            {
                "id": "sleep-performance",
                "title": "睡眠质量与学业表现",
                "summary": "关注两者之间的关联。",
                "subtopics": ["睡眠质量", "学业表现"],
            }
        ],
        scope={"confirmed": {"start_year": 2020, "end_year": 2026, "languages": ["en"]}},
        query_plan={
            "queries": [{"provider": "openalex", "query": "sleep quality academic performance"}]
        },
        model_snapshot={},
    )


def _candidate() -> UnifiedCandidate:
    """构造已经失败、但允许用户单项重试的统一候选。"""
    source = RawCandidate(
        source=SourceName.OPENALEX,
        source_record_id="W-retry",
        title="Sleep quality and academic performance",
        abstract=(
            "The study examines sleep quality and academic performance among university students."
        ),
    )
    return UnifiedCandidate(
        candidate_id=_CANDIDATE_ID,
        title=source.title,
        title_key="sleep quality academic performance",
        abstract=source.abstract,
        source_records=(source,),
        triage=TriageDecision(included=True),
        relevance_state=CandidateRelevanceState.FAILED,
        relevance_error=CandidateRelevanceError(
            code="candidate_relevance_model_unavailable",
            message="候选相关性模型暂时不可用，请稍后重试。",
            retryable=True,
        ),
    )


@pytest.mark.asyncio
async def test_retry_reuses_server_snapshot_and_is_idempotent_after_success() -> None:
    """单项重试只读取已有会话候选，成功后再次点击不会重复调用模型。"""
    candidate = _candidate()
    store = FakeSessionStore(
        {
            "status": "completed",
            "candidate_counts": {"relevance_total_count": 1, "relevance_failed_count": 1},
            "candidates": [candidate.model_dump(mode="json")],
        }
    )
    model = FakeModel()
    evaluator = OpenAICompatibleCandidateRelevanceEvaluator(
        WorkflowSettings(deepseek_api_key=SecretStr("test")),
        model=model,
    )
    service = CandidateRelevanceService(
        cast(AsyncSession, FakeSession(_run(), _plan())),
        cast(SearchSessionStore, store),
        evaluator=evaluator,
    )

    result = await service.retry(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_id=_CANDIDATE_ID,
    )

    retried = UnifiedCandidate.model_validate(result.snapshot["candidates"][0])
    assert model.calls == 1
    assert len(store.writes) == 2
    assert retried.relevance_state is CandidateRelevanceState.COMPLETED
    assert retried.relevance_assessment is not None
    assert retried.relevance_assessment.study_focus.startswith("考察睡眠质量")
    assert result.snapshot["candidate_counts"]["relevance_failed_count"] == 0

    # 构造一个只需读取授权运行的服务，模拟用户在成功后再次点击重试按钮。
    repeated = CandidateRelevanceService(
        cast(AsyncSession, FakeSession(_run(), _plan())),
        cast(SearchSessionStore, store),
        evaluator=evaluator,
    )
    result = await repeated.retry(
        owner_user_id=_OWNER_ID,
        collection_id=_COLLECTION_ID,
        search_run_id=_RUN_ID,
        candidate_id=_CANDIDATE_ID,
    )

    assert model.calls == 1
    assert UnifiedCandidate.model_validate(result.snapshot["candidates"][0]).relevance_state is (
        CandidateRelevanceState.COMPLETED
    )
