"""LangGraph RAG 研究运行的独立 arq Worker。"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, replace
from typing import Any, cast
from uuid import UUID

from app.core.ingestion_settings import IngestionSettings, get_ingestion_settings
from app.core.workflow_settings import WorkflowSettings, get_workflow_settings
from app.infra.db.repositories.research_execution import (
    SqlAlchemyResearchExecutionAdapter,
)
from app.infra.db.repositories.research_retrieval import (
    SqlAlchemyResearchRetrievalRepository,
)
from app.infra.db.research_checkpoint import PostgresResearchGraphExecutor
from app.infra.db.session import async_session_factory
from app.infra.llm.embeddings import OpenAICompatibleTextEmbedder
from app.infra.llm.reranker import HttpResearchReranker
from app.infra.llm.research_model import OpenAICompatibleResearchModel
from app.infra.milvus.research_search import MilvusResearchVectorSearch
from app.infra.redis.connection import (
    redis_client_from_environment,
    redis_settings_from_environment,
)
from app.infra.redis.queues import RESEARCH_QUEUE_NAME
from app.infra.redis.research_events import RedisResearchEventStore
from app.modules.agents.contracts import (
    ResearchChatModel,
    ResearchModelError,
    ResearchModelProtocolError,
    ResearchRunCancelled,
)
from app.modules.agents.fast_rag import FastRagRunner
from app.modules.agents.graph import ResearchGraphRunner
from app.modules.agents.state import ResearchGraphOutcome
from app.modules.rag.retrieval import (
    ResearchReranker,
    ResearchRerankerError,
    ResearchRetriever,
    RetrievalUnavailableError,
)
from app.modules.research.contracts import (
    ResearchProgressEvent,
    ResearchRunStage,
    ResearchRunStatus,
)
from app.modules.research.question_mode import (
    ResearchExecutionMode,
    ResearchModeDecision,
    research_question_mode_from_config,
    resolve_research_execution_mode,
)
from app.modules.research.settings import ResearchSettings, get_research_settings

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
    reranker: ResearchReranker | None
    model: ResearchChatModel


@dataclass(frozen=True, slots=True)
class ResearchFailureDetails:
    """Worker 写入失败终态所需的稳定错误字段。"""

    code: str
    message: str
    diagnostics: dict[str, Any] | None


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
        reranker=(
            HttpResearchReranker(research_settings) if research_settings.reranker_enabled else None
        ),
        model=OpenAICompatibleResearchModel(workflow_settings, research_settings),
    )
    await _finalize_interrupted_cancellations(research_settings)


async def _finalize_interrupted_cancellations(settings: ResearchSettings) -> None:
    """Worker 重启后回收已请求取消、但上一进程未能确认终态的运行。"""
    async with async_session_factory() as session:
        run_ids = await SqlAlchemyResearchExecutionAdapter(
            session
        ).finalize_requested_cancellations()
    if not run_ids:
        return
    logger.warning("已回收 %s 条悬挂的研究取消请求。", len(run_ids))
    for run_id in run_ids:
        try:
            await _publish_terminal_event(
                run_id=run_id,
                settings=settings,
                status=ResearchRunStatus.CANCELLED,
                stage=ResearchRunStage.CANCELLED,
                message="研究任务已取消。",
                evidence_count=0,
            )
        except Exception:
            logger.exception("研究取消回收终态事件发布失败：research_run_id=%s", run_id)


async def run_research(ctx: dict[str, Any], research_run_id: str) -> dict[str, str | int]:
    """领取一条运行，执行受限图，并在每个公开阶段同步持久化状态与 SSE 事件。"""
    try:
        run_id = UUID(research_run_id)
    except ValueError as exc:
        raise ValueError("arq 研究任务缺少合法的 research_run_id。") from exc

    dependencies = cast(ResearchWorkerDependencies, ctx["research_dependencies"])
    async with async_session_factory() as session:
        context = await SqlAlchemyResearchExecutionAdapter(session).claim(run_id)
    if context is None:
        return {"research_run_id": str(run_id), "status": "ignored", "evidence_count": 0}

    async def publish_stage(
        stage: ResearchRunStage, message: str | None, evidence_count: int
    ) -> None:
        """每次图节点进入公开阶段时更新 PostgreSQL，再写入短期 Redis 事件。"""
        async with async_session_factory() as stage_session:
            execution = SqlAlchemyResearchExecutionAdapter(stage_session)
            updated = await execution.set_stage(run_id, stage)
        if not updated:
            async with async_session_factory() as cancellation_session:
                cancelled = await SqlAlchemyResearchExecutionAdapter(
                    cancellation_session
                ).is_cancel_requested(run_id)
            if cancelled:
                raise ResearchRunCancelled("研究运行已在阶段边界请求停止。")
            return
        redis = redis_client_from_environment()
        try:
            await RedisResearchEventStore(
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

    async def cancellation_requested() -> bool:
        """让图在每个模型/检索调用边界读取 PostgreSQL 中的协作取消标记。"""
        async with async_session_factory() as cancellation_session:
            return await SqlAlchemyResearchExecutionAdapter(
                cancellation_session
            ).is_cancel_requested(run_id)

    try:
        async with async_session_factory() as retrieval_session:
            retriever = ResearchRetriever(
                SqlAlchemyResearchRetrievalRepository(retrieval_session),
                embedder=dependencies.embedder,
                vector_search=dependencies.vector_search,
                settings=dependencies.research_settings,
                reranker=dependencies.reranker,
            )
            mode_decision = resolve_research_execution_mode(
                context.question,
                research_question_mode_from_config(context.model_config),
            )
            if mode_decision.execution_mode is ResearchExecutionMode.FAST_RAG:
                outcome = await FastRagRunner(
                    retriever=retriever,
                    model=dependencies.model,
                    settings=dependencies.research_settings,
                    stage_callback=publish_stage,
                    cancellation_checker=cancellation_requested,
                ).run(context, mode_decision)
            else:
                strict_outcome = await ResearchGraphRunner(
                    retriever=retriever,
                    model=dependencies.model,
                    settings=dependencies.research_settings,
                    graph_executor=PostgresResearchGraphExecutor(
                        dependencies.research_settings.checkpoint_database_url
                    ),
                    stage_callback=publish_stage,
                    cancellation_checker=cancellation_requested,
                ).run(context)
                outcome = _with_strict_research_trace(strict_outcome, mode_decision)
    except ResearchRunCancelled:
        async with async_session_factory() as cancellation_session:
            cancelled = await SqlAlchemyResearchExecutionAdapter(
                cancellation_session
            ).finalize_cancellation(run_id)
        if cancelled:
            await _publish_terminal_event(
                run_id=run_id,
                settings=dependencies.research_settings,
                status=ResearchRunStatus.CANCELLED,
                stage=ResearchRunStage.CANCELLED,
                message="研究任务已在安全执行边界停止。",
                evidence_count=0,
            )
        return {"research_run_id": str(run_id), "status": "cancelled", "evidence_count": 0}
    except ResearchModelProtocolError as exc:
        logger.exception("研究模型返回了不符合证据协议的结构化回答：research_run_id=%s", run_id)
        error_code = "research_model_protocol_failed"
        error_message = "研究模型返回了无法核验的回答，请重试。"
        failure_diagnostics = exc.diagnostics
    except ResearchModelError:
        logger.exception("研究模型未生成可核验的结构化回答：research_run_id=%s", run_id)
        error_code = "research_model_failed"
        error_message = "研究模型调用失败，未生成可核验回答。"
        failure_diagnostics = None
    except Exception as exc:
        # 详细堆栈只写入 Worker 日志，数据库和 API 继续返回稳定、无敏感信息的错误。
        failure = _failure_from_unexpected_exception(exc)
        logger.exception(
            "研究运行执行失败：research_run_id=%s failure_code=%s error_type=%s",
            run_id,
            failure.code,
            exc.__class__.__name__,
        )
        error_code = failure.code
        error_message = failure.message
        failure_diagnostics = failure.diagnostics
    else:
        async with async_session_factory() as completion_session:
            persisted_status = await SqlAlchemyResearchExecutionAdapter(
                completion_session
            ).complete(run_id, outcome)
        if persisted_status is None:
            return {"research_run_id": str(run_id), "status": "ignored", "evidence_count": 0}
        if persisted_status is ResearchRunStatus.CANCELLED:
            await _publish_terminal_event(
                run_id=run_id,
                settings=dependencies.research_settings,
                status=ResearchRunStatus.CANCELLED,
                stage=ResearchRunStage.CANCELLED,
                message="研究任务已在安全执行边界停止。",
                evidence_count=0,
            )
            return {"research_run_id": str(run_id), "status": "cancelled", "evidence_count": 0}
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
        terminal_status = await SqlAlchemyResearchExecutionAdapter(failure_session).fail(
            run_id,
            code=error_code,
            message=error_message,
            diagnostics=failure_diagnostics,
        )
    if terminal_status is ResearchRunStatus.CANCELLED:
        await _publish_terminal_event(
            run_id=run_id,
            settings=dependencies.research_settings,
            status=ResearchRunStatus.CANCELLED,
            stage=ResearchRunStage.CANCELLED,
            message="研究任务已在安全执行边界停止。",
            evidence_count=0,
        )
        return {"research_run_id": str(run_id), "status": "cancelled", "evidence_count": 0}
    if terminal_status is ResearchRunStatus.FAILED:
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
        await RedisResearchEventStore(redis, ttl_seconds=settings.rag_event_ttl_seconds).publish(
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


def _with_strict_research_trace(
    outcome: ResearchGraphOutcome, decision: ResearchModeDecision
) -> ResearchGraphOutcome:
    """Preserve the old graph mode while exposing that the strict chain was selected."""
    trace = {
        **outcome.retrieval_trace,
        "execution_mode": ResearchExecutionMode.STRICT_RESEARCH.value,
        "requested_mode": decision.requested_mode.value,
        "execution_routing": decision.to_trace(),
    }
    if outcome.status is ResearchRunStatus.COMPLETED:
        trace = {
            **trace,
            "citation_checked": bool(outcome.cited_chunk_ids),
            "claim_verified": "answer_claim_verification" in trace or "answer_repair" in trace,
        }
    return replace(outcome, retrieval_trace=trace)


def _failure_from_unexpected_exception(exc: Exception) -> ResearchFailureDetails:
    """把跨外部依赖的异常压缩为可展示、可排查且不含敏感信息的失败详情。"""
    if isinstance(exc, RetrievalUnavailableError):
        return ResearchFailureDetails(
            code=exc.code,
            message=str(exc) or "当前集合暂时无法检索文献证据，请稍后重试。",
            diagnostics={
                "component": "retrieval",
                "error_type": exc.__class__.__name__,
            },
        )
    if isinstance(exc, ResearchRerankerError):
        return ResearchFailureDetails(
            code="research_reranker_failed",
            message="证据重排服务暂时不可用，请稍后重试。",
            diagnostics={
                "component": "reranker",
                "error_type": exc.__class__.__name__,
            },
        )
    return ResearchFailureDetails(
        code="research_execution_failed",
        message="研究任务执行失败，请稍后重试。",
        diagnostics=None,
    )


class WorkerSettings:
    """供 ``arq app.workers.research.WorkerSettings`` 启动的专用研究 Worker。"""

    functions = [run_research]
    on_startup = startup
    redis_settings = redis_settings_from_environment()
    queue_name = RESEARCH_QUEUE_NAME
    max_jobs = 1
    max_tries = 1
    job_timeout = 600
