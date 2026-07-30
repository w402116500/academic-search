"""外部文献来源的进程内请求节流器。"""

from __future__ import annotations

import asyncio
from time import monotonic


class InProcessRequestThrottle:
    """在单个 API 进程内保证同一来源请求之间存在最小间隔。

    首版使用它保护本地开发和单进程演示。多 Worker 部署时会在后续接入 Redis
    的跨进程令牌桶；Provider 无需知道底层限流实现如何替换。
    """

    def __init__(self, minimum_interval_seconds: float) -> None:
        """保存最小间隔；调用方应在构造前完成正数配置校验。"""
        self._minimum_interval_seconds = minimum_interval_seconds
        self._lock = asyncio.Lock()
        self._last_request_at: float | None = None

    @classmethod
    def from_requests_per_minute(cls, requests_per_minute: int) -> InProcessRequestThrottle:
        """将每分钟上限转换为相邻请求之间的最小时间间隔。"""
        return cls(minimum_interval_seconds=60 / requests_per_minute)

    async def wait_for_slot(self) -> None:
        """等待并占用一个请求时隙，避免并发协程突破来源限制。"""
        async with self._lock:
            now = monotonic()

            if self._last_request_at is not None:
                elapsed = now - self._last_request_at
                delay = self._minimum_interval_seconds - elapsed

                if delay > 0:
                    await asyncio.sleep(delay)

            # 睡眠结束后重新计时，确保下一个请求与真实发起时间保持最小间隔。
            self._last_request_at = monotonic()
