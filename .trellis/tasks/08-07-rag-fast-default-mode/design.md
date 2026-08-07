# RAG 默认快速问答模式设计

## Architecture

本任务把现有 RAG 执行语义拆成两个公开模式：

- `fast_rag`：默认快速引用问答。复用现有 retriever、writer、EvidenceRef 协议校验和 citation render，不默认执行 LLM claim verifier / repair / presentation edit。
- `strict_research`：现有严格研究链路。保留 `route_question()`、single RAG 完整 verifier/repair、multi-agent 规划与核验能力。

用户可通过前端模式选择显式请求快速问答或深度研究。自动模式只在强复杂意图命中时进入 `strict_research`，否则进入 `fast_rag`。

## Backend Flow

新增轻量模式解析层，位置应尽量靠近 research execution 入口，避免 Fast RAG 仍然经过 `ResearchGraphRunner.run()` 的前置 `route_question()`：

```text
Research Worker
-> claim run
-> build retriever/model
-> dispatch by requested mode / strong complexity rules
   -> fast_rag runner
   -> strict ResearchGraphRunner
-> complete/fail/cancel
-> publish SSE final state
```

Fast RAG runner 目标流程：

```text
PREPARING
-> HYBRID_RETRIEVAL
-> PARENT_MERGING/RERANKING
-> ANSWERING
-> deterministic citation validation
-> COMPLETED or AWAITING_CLARIFICATION
```

Strict Research 继续使用现有 `ResearchGraphRunner`。

## Routing Contract

请求模式建议为：

- `fast`：强制 Fast RAG。
- `strict`：强制 Strict Research。
- `auto`：后端规则判断；若前端暂不传值，则默认等价于 `fast` 或 `auto` 中的 Fast 结果。

自动规则：

```text
if requested == strict:
    strict_research
elif requested == fast:
    fast_rag
elif strong_complex_intent(question):
    strict_research
else:
    fast_rag
```

强复杂意图包括：比较、对比、差异、优缺点、跨论文综合、综述、多篇论文、证据冲突、结论是否一致、逐条核验、严格核验、多个维度同时要求等。

灰区示例“总结这篇论文”“创新点是什么”“为什么有效”默认走 Fast RAG。

## Citation Contract

Fast RAG 必须复用现有 evidence snapshot 与 EvidenceRef 规则：

- 模型侧只能引用本轮快照中的 `E1/E2/...`。
- 回答正文中的 `【E1】` 与结构化 `cited_refs` 必须一致。
- 渲染用户引用时按首次出现顺序映射为 `[1]/[2]`。
- 最终 `cited_chunk_ids` 必须来自真实 chunk UUID。
- 没有可展示引用不能作为成功答案完成。

Fast RAG 不做逐 claim 语义支持判断，因此 trace 需要明确：

```json
{
  "mode": "fast_rag",
  "citation_checked": true,
  "claim_verified": false
}
```

## Frontend Flow

输入区增加模式选择，默认“快速问答”。

- 快速问答：发送 `mode=fast` 或项目 API 约定的等价值。
- 深度研究：发送 `mode=strict`。

前端应显示当前运行模式，避免用户误以为快速问答也执行了逐 claim 审计。

## Compatibility

- 不改变现有已保存 answer/citations 的展示格式。
- 若旧客户端未传模式，后端按快速默认处理。
- Strict Research 保留现有 trace 字段，并可新增 `mode=strict_research` / `routing.source` 等兼容字段。

## Repository Housekeeping

本轮架构讨论确认 2026-08-05 的边界重排后，旧目录
`backend/app/db`、`backend/app/modules/collections`、
`backend/app/modules/fulltext`、`backend/app/modules/ingestion` 和
`backend/app/modules/workflow` 已迁移到新的 `infra/db`、`research`、
`documents`、`rag/ingestion`、`search` 与 `agents` 所有者下。若这些旧路径
只剩 `__pycache__` 或空目录，应清理而不是恢复；后续文档只描述当前所有者。

## Risks

- Fast RAG 可信边界降低：引用来源可校验，但 claim 是否被引用文本充分支持不再由二次 LLM 默认审计。通过 UI/trace 命名和深度研究模式缓解。
- 路由规则误判：灰区默认 Fast 是有意选择，用户可显式切换深度研究。
- 现有 `ResearchRunMode` / API 枚举可能只包含 `single_rag` / `multi_agent`，实现时需兼容历史值与新请求语义。

## Rollback

- 后端保留 Strict Research 原链路，必要时可通过配置或前端默认值切回严格链路。
- 若 Fast RAG 出现引用协议问题，确定性校验会失败进入错误/澄清路径，不应落库为成功引用答案。
