# ADR 0001: 模块化单体的所有权与依赖方向

- 状态：Accepted
- 日期：2026-08-05
- 关联：[`01-product-direction-discussion.md`](../01-product-direction-discussion.md)

## 背景

项目已形成可运行的 API、异步 Worker、检索、全文入库和研究问答闭环，但部分基础设施实现进入业务模块，`workflow` 同时承担多个产品域，并出现 `modules -> workers` 和业务模块双向依赖。目录名称存在并不等于边界生效，因此需要用所有权和可检查的依赖规则约束后续演进。

## 决策

采用模块化单体，依赖只允许从外层装配入口指向业务端口，再由基础设施实现端口：

```text
api/routers -> api/deps ┐
workers ----------------┼-> modules ports/services
                        └-> infra adapters -> external systems
```

业务所有权固定如下：

| 事实或用例 | 所有者 |
| --- | --- |
| 工作区、研究计划、集合、会话 | `modules/research` |
| search run、相关性和候选审核 | `modules/search` |
| 引用、论文事实和准入 | `modules/literature` |
| 全文、文件与版本 | `modules/documents` |
| 入库、检索与证据 | `modules/rag` |
| LangGraph 图和节点 | `modules/agents` |

具体约束：

1. `modules` 可以依赖 `core` 和其他模块公开的 contract/facade，不得依赖 `api`、`workers` 或 `infra`。
2. `infra` 可以依赖模块端口并提供 SQLAlchemy、Redis、arq、Milvus、Boto3、LLM、Reranker 和 checkpoint 实现。
3. `api/routers` 只做协议映射，具名 `api/deps` 负责请求级装配和资源释放。
4. Worker 是进程级 composition root，只做 payload 校验、装配、调用和重试边界。
5. Agent 节点通过 RAG/research 端口访问能力，不直接创建数据库、向量库、模型或对象存储客户端。
6. 前端 route view 只组合 feature；Query hooks 位于 `api/hooks`，OpenAPI 生成类型是 wire contract 的唯一来源。

## 文件重量规则

普通业务源文件建议不超过 600 行，超过 700 行必须审查职责，超过 1000 行必须拆分。生成代码、OpenAPI schema 和 Alembic migration 排除。拆分以所有权和独立变化原因为依据，不为满足行数机械创建通用 helper。

## 迁移策略

当前 HTTP/SSE、数据库 schema、Redis key 和队列 payload 保持兼容。迁移按依赖反转、Infra 归位、业务模块归位、OpenAPI 类型生成、前端 feature 化的顺序进行；每一批更新全部调用者并删除旧实现，不保留第二套事实来源。

## 结果

- 业务规则可以脱离具体基础设施验证，路由和 Worker 不再复制装配逻辑。
- 状态变化只修改真实所有者及其公开 contract，降低跨模块脆弱性。
- 静态依赖、文件重量和 OpenAPI 漂移检查进入 CI；这些检查不通过增加重复业务测试来替代。
