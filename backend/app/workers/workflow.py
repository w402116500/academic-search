"""研究意图分析的 arq Worker 配置与任务函数。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.db.session import async_session_factory
from app.modules.workflow.intent_analysis import (
    IntentAnalysisError,
    OpenAICompatibleIntentAnalyzer,
)
from app.modules.workflow.plan_service import ResearchPlanService
from app.modules.workflow.settings import get_workflow_settings
from app.workers.fulltext import acquire_candidate_fulltext
from app.workers.queues import WORKFLOW_QUEUE_NAME
from app.workers.redis import redis_settings_from_environment
from app.workers.search import run_search


async def analyze_research_plan(
    _ctx: dict[str, Any],
    research_plan_id: str,
) -> dict[str, str]:
    """生成一个持久化计划草稿；不直接启动检索或绕过用户确认。"""
    try:
        plan_id = UUID(research_plan_id)
    except ValueError as exc:
        raise ValueError("arq 研究计划分析任务缺少合法的 research_plan_id。") from exc

    async with async_session_factory() as session:
        service = ResearchPlanService(session)
        plan = await service.get_plan_for_analysis(plan_id)
        if plan is None:
            # 已完成、已替代或已删除计划的重复队列消息不应改写当前状态。
            return {"research_plan_id": str(plan_id), "status": "ignored"}

        try:
            analyzer = OpenAICompatibleIntentAnalyzer(get_workflow_settings())
            result = await analyzer.analyze(plan.raw_request)
        except IntentAnalysisError as exc:
            await service.fail_analysis(
                research_plan_id=plan_id,
                error_code=exc.code.value,
                error_message=str(exc),
            )
            return {"research_plan_id": str(plan_id), "status": "failed"}
        except Exception as exc:
            # 配置和非预期程序错误必须写入状态，同时保留异常以便 arq 日志定位根因。
            await service.fail_analysis(
                research_plan_id=plan_id,
                error_code="intent_analysis_unexpected_error",
                error_message="研究意图分析发生未预期错误，请重新生成计划。",
            )
            raise RuntimeError("研究意图分析 Worker 执行失败。") from exc

        completed = await service.complete_analysis(research_plan_id=plan_id, result=result)
        return {
            "research_plan_id": str(plan_id),
            "status": "ready" if completed is not None else "ignored",
        }


class WorkerSettings:
    """供 ``arq app.workers.workflow.WorkerSettings`` 启动的独立分析 Worker。"""

    # 意图分析、检索和全文获取共用一个 arq 队列，避免不同 Worker 抢到未知任务。
    functions = [analyze_research_plan, run_search, acquire_candidate_fulltext]
    redis_settings = redis_settings_from_environment()
    queue_name = WORKFLOW_QUEUE_NAME
    max_jobs = 2
    # 失败会明确写入计划状态，用户可修改要求后生成新版本，因此不静默自动重试。
    max_tries = 1
    job_timeout = 240
