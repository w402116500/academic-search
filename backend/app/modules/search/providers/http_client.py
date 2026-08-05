"""文献来源共用的显式 HTTP 客户端创建逻辑。"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from app.core.settings import ProviderNetworkSettings


def create_provider_async_client(
    *,
    base_url: str = "",
    headers: Mapping[str, str],
    timeout_seconds: float,
    network: ProviderNetworkSettings,
    transport: httpx.AsyncBaseTransport | None = None,
    follow_redirects: bool = False,
) -> httpx.AsyncClient:
    """创建不会意外继承进程全局代理变量的 Provider HTTP 客户端。

    代理地址只来自已校验的 ``network.proxy_url``。即使运行进程设置了
    HTTP_PROXY 或 HTTPS_PROXY，``trust_env=False`` 也能保证直连来源不受影响。
    """
    if network.mode == "proxy" and network.proxy_url is None:
        raise ValueError("proxy 网络路由缺少代理地址")

    return httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=httpx.Timeout(timeout_seconds),
        transport=transport,
        proxy=network.proxy_url,
        trust_env=False,
        follow_redirects=follow_redirects,
    )
