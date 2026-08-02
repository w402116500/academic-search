"""向 RAG 入库 Worker 投递任务的 arq 适配器。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.workers.queues import INGESTION_QUEUE_NAME
from app.workers.redis import redis_settings_from_environment
from arq import create_pool


class IngestionJobQueue(Protocol):
    """集合构建服务依赖的最小投递接口，便于替换为离线测试替身。"""

    async def enqueue_ingestion(self, ingestion_run_id: UUID) -> str:
        """投递一次已确认的入库运行，并返回可审计的 arq 任务标识。"""
        raise NotImplementedError


class IngestionQueueError(RuntimeError):
    """Redis 或 arq 无法接收入库任务时抛出的明确基础设施错误。"""


class ArqIngestionJobQueue:
    """以短连接方式向专用入库 Worker 投递任务。"""

    async def enqueue_ingestion(self, ingestion_run_id: UUID) -> str:
        """使用运行 UUID 形成幂等任务标识，避免双击重复创建 Worker 工作。"""
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
            # arq 返回 None 代表同一任务已存在；对同一运行而言这是幂等成功。
            return job.job_id if job is not None else job_id
        except Exception as exc:
            raise IngestionQueueError("文献入库任务无法投递到 Redis。") from exc
        finally:
            if redis is not None:
                await redis.aclose(close_connection_pool=True)
