"""向独立 RAG 研究 Worker 投递任务的 arq 适配器。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.workers.queues import RESEARCH_QUEUE_NAME
from app.workers.redis import redis_settings_from_environment
from arq import create_pool


class ResearchJobQueue(Protocol):
    """会话服务依赖的最小异步任务边界，离线测试可使用内存替身。"""

    async def enqueue_research(self, research_run_id: UUID) -> str:
        """投递一条已持久化研究运行并返回稳定 arq 任务标识。"""
        raise NotImplementedError


class ResearchQueueError(RuntimeError):
    """Redis 或 arq 无法接收研究任务时抛出的基础设施错误。"""


class ArqResearchJobQueue:
    """以短连接向专用研究队列投递任务，避免 Web 进程隐式持有 Redis 状态。"""

    async def enqueue_research(self, research_run_id: UUID) -> str:
        """使用运行 UUID 保证双击或网络重试不会创建重复研究任务。"""
        redis = None
        job_id = f"research-{research_run_id}"
        try:
            redis = await create_pool(redis_settings_from_environment())
            job = await redis.enqueue_job(
                "run_research",
                str(research_run_id),
                _job_id=job_id,
                _queue_name=RESEARCH_QUEUE_NAME,
            )
            # arq 返回 None 表示相同任务已在队列中；该运行仍是幂等成功。
            return job.job_id if job is not None else job_id
        except Exception as exc:
            raise ResearchQueueError("研究对话任务无法投递到 Redis。") from exc
        finally:
            if redis is not None:
                await redis.aclose(close_connection_pool=True)
