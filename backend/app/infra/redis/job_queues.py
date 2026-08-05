"""arq adapters for all product-owned asynchronous task ports."""

from __future__ import annotations

from uuid import UUID, uuid4

from arq import create_pool

from app.infra.redis.connection import redis_settings_from_environment
from app.infra.redis.queues import (
    INGESTION_QUEUE_NAME,
    RELEVANCE_QUEUE_NAME,
    RESEARCH_QUEUE_NAME,
    WORKFLOW_QUEUE_NAME,
)
from app.modules.documents.queue import CandidateFulltextQueueError
from app.modules.rag.ingestion.queue import IngestionQueueError
from app.modules.research.queue import ResearchPlanQueueError, ResearchQueueError
from app.modules.search.queue import CandidateRelevanceQueueError, SearchRunQueueError


class ArqResearchPlanJobQueue:
    """每次投递短暂打开 Redis 连接，避免 Web 进程维护隐藏的全局连接状态。"""

    async def enqueue_analysis(self, research_plan_id: UUID) -> str:
        """用计划 UUID 作为幂等 job ID，防止同一版本被重复投递。"""
        redis = None
        try:
            redis = await create_pool(redis_settings_from_environment())
            job = await redis.enqueue_job(
                "analyze_research_plan",
                str(research_plan_id),
                _job_id=str(research_plan_id),
                _queue_name=WORKFLOW_QUEUE_NAME,
            )
            if job is None:
                raise ResearchPlanQueueError("研究计划分析任务已存在或无法投递。")
            return job.job_id
        except ResearchPlanQueueError:
            raise
        except Exception as exc:
            # 连接和协议异常在队列边界转成业务错误；调用方会把计划明确标记为失败。
            raise ResearchPlanQueueError("研究计划分析任务无法投递到 Redis。") from exc
        finally:
            if redis is not None:
                await redis.aclose(close_connection_pool=True)


class ArqSearchRunJobQueue:
    """向独立的检索 Worker 投递任务；每次调用后释放短暂 Redis 连接。"""

    async def enqueue_search(self, search_run_id: UUID) -> str:
        """使用 SearchRun UUID 作为幂等 job ID，避免重复投递同一运行。"""
        redis = None
        try:
            redis = await create_pool(redis_settings_from_environment())
            job = await redis.enqueue_job(
                "run_search",
                str(search_run_id),
                _job_id=str(search_run_id),
                _queue_name=WORKFLOW_QUEUE_NAME,
            )
            if job is None:
                raise SearchRunQueueError("文献检索任务已存在或无法投递。")
            return job.job_id
        except SearchRunQueueError:
            raise
        except Exception as exc:
            raise SearchRunQueueError("文献检索任务无法投递到 Redis。") from exc
        finally:
            if redis is not None:
                await redis.aclose(close_connection_pool=True)


class ArqCandidateFulltextJobQueue:
    """向工作流 Worker 投递短期全文下载任务。"""

    async def enqueue_fulltext(
        self,
        *,
        search_run_id: UUID,
        candidate_id: UUID,
        attempt_no: int,
    ) -> str:
        """使用运行、候选和尝试序号组成幂等 Job ID，允许失败后创建新尝试。"""
        redis = None
        job_id = f"fulltext-{search_run_id}-{candidate_id}-{attempt_no}"
        try:
            redis = await create_pool(redis_settings_from_environment())
            job = await redis.enqueue_job(
                "acquire_candidate_fulltext",
                str(search_run_id),
                str(candidate_id),
                attempt_no,
                _job_id=job_id,
                _queue_name=WORKFLOW_QUEUE_NAME,
            )
            # arq 返回 None 说明相同尝试已排队；这对重复点击是幂等成功。
            return job.job_id if job is not None else job_id
        except Exception as exc:
            raise CandidateFulltextQueueError("全文获取任务无法投递到 Redis。") from exc
        finally:
            if redis is not None:
                await redis.aclose(close_connection_pool=True)


class ArqCandidateRelevanceJobQueue:
    """向专用 relevance Worker 投递完整候选集合任务。"""

    async def enqueue_relevance(self, *, search_run_id: UUID, attempt_no: int) -> str:
        """使用稳定尝试序号投递完整集合，重复投递同一轮保持幂等。"""
        redis = None
        job_id = f"relevance-{search_run_id}-{attempt_no}"
        try:
            redis = await create_pool(redis_settings_from_environment())
            job = await redis.enqueue_job(
                "run_candidate_relevance",
                str(search_run_id),
                attempt_no,
                _job_id=job_id,
                _queue_name=RELEVANCE_QUEUE_NAME,
            )
            return job.job_id if job is not None else job_id
        except Exception as exc:
            raise CandidateRelevanceQueueError("候选相关性任务无法投递到 Redis。") from exc
        finally:
            if redis is not None:
                await redis.aclose(close_connection_pool=True)


class ArqIngestionJobQueue:
    """Enqueue idempotent document-ingestion runs on the dedicated queue."""

    async def enqueue_ingestion(self, ingestion_run_id: UUID) -> str:
        redis = None
        job_id = f"ingestion-{ingestion_run_id}"
        try:
            redis = await create_pool(redis_settings_from_environment())
            job = await redis.enqueue_job(
                "ingest_document",
                str(ingestion_run_id),
                _job_id=job_id,
                _queue_name=INGESTION_QUEUE_NAME,
            )
            return job.job_id if job is not None else job_id
        except Exception as exc:
            raise IngestionQueueError("文献入库任务无法投递到 Redis。") from exc
        finally:
            if redis is not None:
                await redis.aclose(close_connection_pool=True)


class ArqResearchJobQueue:
    """Enqueue research runs on the dedicated queue with explicit retry identity."""

    async def enqueue_research(self, research_run_id: UUID, *, retry: bool = False) -> str:
        redis = None
        job_id = f"research-{research_run_id}"
        if retry:
            job_id = f"{job_id}-retry-{uuid4().hex}"
        try:
            redis = await create_pool(redis_settings_from_environment())
            job = await redis.enqueue_job(
                "run_research",
                str(research_run_id),
                _job_id=job_id,
                _queue_name=RESEARCH_QUEUE_NAME,
            )
            return job.job_id if job is not None else job_id
        except Exception as exc:
            raise ResearchQueueError("研究对话任务无法投递到 Redis。") from exc
        finally:
            if redis is not None:
                await redis.aclose(close_connection_pool=True)
