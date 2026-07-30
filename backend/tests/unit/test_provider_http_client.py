"""文献来源共用 HTTP 客户端的网络路由测试。"""

import httpx
import pytest
from app.core.settings import ProviderNetworkSettings
from app.modules.search.providers.http_client import create_provider_async_client


@pytest.mark.parametrize(
    ("network", "expected_proxy"),
    [
        (ProviderNetworkSettings(mode="direct", proxy_url=None), None),
        (
            ProviderNetworkSettings(mode="proxy", proxy_url="http://127.0.0.1:7897"),
            "http://127.0.0.1:7897",
        ),
    ],
)
def test_provider_http_client_uses_only_the_explicit_network_route(
    monkeypatch: pytest.MonkeyPatch,
    network: ProviderNetworkSettings,
    expected_proxy: str | None,
) -> None:
    """客户端始终关闭 trust_env，并只使用配置对象中明确给出的代理地址。"""
    observed_options: list[dict[str, object]] = []

    class RecordingAsyncClient:
        """替代真实客户端，只记录工厂传入的构造参数。"""

        def __init__(self, **kwargs: object) -> None:
            observed_options.append(kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", RecordingAsyncClient)
    create_provider_async_client(
        base_url="https://example.com",
        headers={"Accept": "application/json"},
        timeout_seconds=10,
        network=network,
    )

    assert observed_options[0]["trust_env"] is False
    assert observed_options[0]["proxy"] == expected_proxy
