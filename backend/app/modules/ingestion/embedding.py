"""OpenAI 兼容 embedding 提供方的适配器。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.modules.ingestion.contracts import IngestionError, IngestionErrorCode
from app.modules.ingestion.settings import IngestionSettings
from langchain_openai import OpenAIEmbeddings


class TextEmbedder(Protocol):
    """入库编排器依赖的最小文本嵌入接口。"""

    async def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """为输入文本按原顺序生成等数量向量，失败时抛出 ``IngestionError``。"""
        raise NotImplementedError


class OpenAICompatibleTextEmbedder:
    """通过 LangChain 调用 OpenAI 或兼容服务的文本嵌入接口。"""

    def __init__(self, settings: IngestionSettings) -> None:
        """保存模型连接配置；客户端延迟到首个 embed 阶段创建。"""
        self._batch_size = settings.rag_embedding_batch_size
        self._model = settings.openai_embedding_model
        self._api_key = settings.openai_api_key
        self._base_url = settings.openai_base_url
        self._client: OpenAIEmbeddings | None = None

    async def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """批量生成向量，并校验数量、维度和数值类型以保护 Milvus schema。"""
        if not texts:
            return ()

        try:
            client = self._client or self._create_client()
            self._client = client
            raw_vectors = await client.aembed_documents(list(texts))
        except Exception as exc:
            # 外部模型服务的异常类型并不稳定，在适配器边界统一为可展示错误码。
            raise IngestionError(
                IngestionErrorCode.EMBEDDING_FAILED,
                "嵌入模型调用失败，文献尚未写入向量索引。",
                retryable=True,
            ) from exc

        vectors = tuple(tuple(float(value) for value in vector) for vector in raw_vectors)
        if len(vectors) != len(texts) or not vectors:
            raise IngestionError(
                IngestionErrorCode.EMBEDDING_MISMATCH,
                "嵌入模型返回的向量数量与待索引片段不一致。",
                retryable=True,
            )

        dimension = len(vectors[0])
        if dimension == 0 or any(len(vector) != dimension for vector in vectors):
            raise IngestionError(
                IngestionErrorCode.EMBEDDING_MISMATCH,
                "嵌入模型返回了空向量或维度不一致的向量。",
                retryable=True,
            )

        return vectors

    def _create_client(self) -> OpenAIEmbeddings:
        """在任务真正需要向量时才校验模型凭据，避免空闲 Worker 无法启动。"""
        return OpenAIEmbeddings(
            model=self._model,
            api_key=self._api_key,
            base_url=self._base_url,
            chunk_size=self._batch_size,
        )
