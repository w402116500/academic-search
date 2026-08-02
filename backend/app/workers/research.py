"""LangGraph RAG 研究运行的独立 arq Worker。"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from app.db.session import async_session_factory
from app.modules.ingestion.embedding import OpenAICompatibleTextEmbedder
from app.modules.ingestion.settings import IngestionSettings, get_ingestion_settings
from app.modules.research.contracts import (
    ResearchProgressEvent,
    ResearchRunStage,
    ResearchRunStatus,
)
from app.modules.research.events import ResearchEventStore
from app.modules.research.execution import ResearchExecutionService
from app.modules.research.graph import (
    OpenAICompatibleResearchModel,
    ResearchGraphRunner,
    ResearchModelError,
)
from app.modules.research.retrieval import MilvusResearchVectorSearch, ResearchRetriever
from app.modules.research.settings import ResearchSettings, get_research_settings
from app.modules.workflow.settings import WorkflowSettings, get_workflow_settings
from app.workers.queues import RESEARCH_QUEUE_NAME
from app.workers.redis import redis_client_from_environment, redis_settings_from_environment

logger = logging.getLogger(__name__)

# psycopg 的异步连接不兼容 Windows 的 ProactorEventLoop。arq 会先导入本模块、
# 再创建 Worker 事件循环，因此在此处切换策略可同时覆盖命令行 Worker 与本地开发。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@dataclass(frozen=True, slots=True)
class ResearchWorkerDependencies:
    """研究 Worker 跨 Job 复用的配置与无状态模型/向量适配器。"""

    ingestion_settings: IngestionSettings
    research_settings: ResearchSettings
    workflow_settings: WorkflowSettings
    embedder: OpenAICompatibleTextEmbedder
    vector_search: MilvusResearchVectorSearch
    model: OpenAICompatibleResearchModel


async def startup(ctx: dict[str, Any]) -> None:
    """启动时读取配置；外部模型和向量连接只在任务实际调用时建立。"""
    ingestion_settings = get_ingestion_settings()
    research_settings = get_research_settings()
    workflow_settings = get_workflow_settings()
    ctx["research_dependencies"] = ResearchWorkerDependencies(
        ingestion_settings=ingestion_settings,
        research_settings=research_settings,
        workflow_settings=workflow_settings,
        embedder=OpenAICompatibleTextEmbedder(ingestion_settings),
        vector_search=MilvusResearchVectorSearch(ingestion_settings),
        model=OpenAICompatibleResearchModel(workflow_settings, research_settings),
    )


async def run_research(ctx: dict[str, Any], research_run_id: str) -> dict[str, str | int]:
    """领取一条运行，执行受限图，并在每个公开阶段同步持久化状态与 SSE 事件。"""
    try:
        run_id = UUID(research_run_id)
    except ValueError as exc:
        raise ValueError("arq 研究任务缺少合法的 research_run_id。") from exc

    dependencies = cast(ResearchWorkerDependencies, ctx["research_dependencies"])
    async with async_session_factory() as session:
        context = await ResearchExecutionService(session).claim(run_id)
    if context is None:
        return {"research_run_id": str(run_id), "status": "ignored", "evidence_count": 0}

    async def publish_stage(
        stage: ResearchRunStage, message: str | None, evidence_count: int
    ) -> None:
        """每次图节点进入公开阶段时更新 PostgreSQL，再写入短期 Redis 事件。"""
        async with async_session_factory() as stage_session:
            updated = await ResearchExecutionService(stage_session).set_stage(run_id, stage)
        if not updated:
            return
        redis = redis_client_from_environment()
        try:
            await ResearchEventStore(
                redis, ttl_seconds=dependencies.research_settings.rag_event_ttl_seconds
            ).publish(
                ResearchProgressEvent(
                    run_id=run_id,
                    status=ResearchRunStatus.RUNNING,
                    stage=stage,
                    message=message,
                    evidence_count=evidence_count,
                )
            )
        finally:
            await redis.aclose()

    try:
        async with async_session_factory() as retrieval_session:
            retriever = ResearchRetriever(
                retrieval_session,
                embedder=dependencies.embedder,
                vector_search=dependencies.vector_search,
                settings=dependencies.research_settings,
            )
            outcome = await ResearchGraphRunner(
                retriever=retriever,
                model=dependencies.model,
                settings=dependencies.research_settings,
                checkpoint_database_url=dependencies.research_settings.checkpoint_database_url,
                stage_callback=publish_stage,
            ).run(context)
    except ResearchModelError:
        logger.exception("研究模型未生成可核验的结构化回答：research_run_id=%s", run_id)
        error_code = "research_model_failed"
        error_message = "研究模型调用失败，未生成可核验回答。"
    except Exception:
        # 详细堆栈只写入 Worker 日志，数据库和 API 继续返回稳定、无敏感信息的错误。
        logger.exception("研究运行执行失败：research_run_id=%s", run_id)
        error_code = "research_execution_failed"
        error_message = "研究任务执行失败，请稍后重试。"
    else:
        async with async_session_factory() as completion_session:
            await ResearchExecutionService(completion_session).complete(run_id, outcome)
        await _publish_terminal_event(
            run_id=run_id,
            settings=dependencies.research_settings,
            status=outcome.status,
            stage=outcome.stage,
            message="研究回答已完成。"
            if outcome.status is ResearchRunStatus.COMPLETED
            else "当前集合证据不足，需要补充问题。",
            evidence_count=len(outcome.cited_chunk_ids),
        )
        return {
            "research_run_id": str(run_id),
            "status": outcome.status.value,
            "evidence_count": len(outcome.cited_chunk_ids),
        }

    async with async_session_factory() as failure_session:
        failed = await ResearchExecutionService(failure_session).fail(
            run_id, code=error_code, message=error_message
        )
    if failed:
        await _publish_terminal_event(
            run_id=run_id,
            settings=dependencies.research_settings,
            status=ResearchRunStatus.FAILED,
            stage=ResearchRunStage.FAILED,
            message=error_message,
            evidence_count=0,
        )
    return {"research_run_id": str(run_id), "status": "failed", "evidence_count": 0}


async def _publish_terminal_event(
    *,
    run_id: UUID,
    settings: ResearchSettings,
    status: ResearchRunStatus,
    stage: ResearchRunStage,
    message: str,
    evidence_count: int,
) -> None:
    """终态也写入 Stream，使正在订阅的页面无需额外轮询即可结束动画。"""
    redis = redis_client_from_environment()
    try:
        await ResearchEventStore(redis, ttl_seconds=settings.rag_event_ttl_seconds).publish(
            ResearchProgressEvent(
                run_id=run_id,
                status=status,
                stage=stage,
                message=message,
                evidence_count=evidence_count,
            )
        )
    finally:
        await redis.aclose()


class WorkerSettings:
    """供 ``arq app.workers.research.WorkerSettings`` 启动的专用研究 Worker。"""

    functions = [run_research]
    on_startup = startup
    redis_settings = redis_settings_from_environment()
    queue_name = RESEARCH_QUEUE_NAME
    max_jobs = 1
    max_tries = 1
    job_timeout = 600
