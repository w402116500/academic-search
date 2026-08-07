# SuperMew Fast RAG 参考要点

参考目录：`E:\mystudy\agent_study\SuperMew-tutorial-verify`

## Observations

- `backend/rag/pipeline.py` 使用 LangGraph：`classify_complexity -> retrieve_initial -> grade_documents -> optional rewrite -> retrieve_rewritten -> grade_documents`。
- 简单问题通过 `_simple_question_fast_path_reason()` 本地规则判断，命中后不调用复杂度模型。
- 复杂问题才调用 FAST_MODEL 做复杂度分类和 2-4 个子问题规划；子问题通过 `Send` 并行执行 `retrieve -> grade`，最后 synthesis 合并。
- 检索层 `backend/rag/utils.py` 将 embedding、Milvus hybrid、auto-merge、rerank、dense fallback 和 trace meta 组合在一起。
- Rerank 有 timeout 和失败回退；hybrid 失败后 dense fallback 复用同一个 query embedding。
- `backend/chat/request_context.py` 在 SSE 中发 `rag_step`，每步包含总耗时和阶段耗时。
- `backend/chat/service.py` 在 `/chat/stream` 一开始立刻发“请求已接收”进度，结束后发送 trace / hitl_request / DONE。
- HITL 续跑不重新进入 agent 或复杂度规划，而是使用用户补充做 targeted retrieval。
- `langsmith_eval.py` 主要用于离线评估完整 `chat_with_agent()`，不是在线链路 tracing 包装。

## Tests That Encode Performance Boundaries

- `tests/test_rag_short_circuit.py`：明显简单问题必须跳过复杂度模型。
- `tests/test_rag_short_circuit.py`：无检索结果不触发 rewrite。
- `tests/test_rag_short_circuit.py`：弱证据最多 rewrite 一次。
- `tests/test_rag_latency_guards.py`：dense fallback 复用 query embedding。
- `tests/test_chat_hitl_resume.py`：SSE 立即报告进度；HITL 续跑不重新创建 agent。

## Takeaways For academic-search

- 默认入口不应先调用模型 router；入口 router 本身可能成为最大延迟。
- 灰区默认快速路径，只有强复杂意图或用户显式选择才升档。
- 快速路径需要保留确定性引用校验，但不默认执行逐 claim LLM verifier。
- trace 应公开模式、路由来源、检索漏斗、是否 claim verified，让速度/严谨权衡可见。
