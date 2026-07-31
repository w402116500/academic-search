"""向 arq 投递研究计划意图分析任务的基础设施适配器。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.workers.redis import redis_settings_from_environment
from arq import create_pool


class ResearchPlanJobQueue(Protocol):
    """计划服务依赖的最小投递接口，业务测试可替换为内存替身。"""

    async def enqueue_analysis(self, research_plan_id: UUID) -> str:
        """投递一个计划分析任务，返回可审计的 arq job 标识。"""
        raise NotImplementedError


class ResearchPlanQueueError(RuntimeError):
    """Redis 或 arq 队列无法接收计划分析任务时抛出。"""


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
