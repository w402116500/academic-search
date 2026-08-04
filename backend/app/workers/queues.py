"""arq Worker 队列名称的唯一来源。"""

from __future__ import annotations

# 工作流任务包含意图分析、检索和候选全文下载，必须由同一类 Worker 消费。
WORKFLOW_QUEUE_NAME = "arq:queue:workflow"

# 完整集合相关性调用可能持续较长时间，独立队列避免占用 Provider 检索和全文下载槽位。
RELEVANCE_QUEUE_NAME = "arq:queue:relevance"

# PDF 解析、嵌入与 Milvus 写入资源较重，使用独立队列避免被工作流 Worker 抢占。
INGESTION_QUEUE_NAME = "arq:queue:ingestion"

# RAG 研究运行会调用向量库和聊天模型，独立队列避免影响检索和 PDF 入库吞吐。
RESEARCH_QUEUE_NAME = "arq:queue:research"
