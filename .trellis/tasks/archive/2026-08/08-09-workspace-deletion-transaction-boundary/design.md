# 修复工作区删除事务边界 - Design

## Problem Statement

工作区删除仓储把 `AsyncSession` 进入方法时一定无事务当成前提。但真实 API 请求会先通过认证依赖查用户，这个只读查询已经触发 SQLAlchemy autobegin。删除仓储随后无条件 `session.begin()`，导致同一个 session 上嵌套开启事务并失败。

## Boundaries

- 修改范围限定在 backend 持久化适配器和测试。
- 领域服务 `ResearchWorkspaceDeletionService` 的编排顺序保持不变。
- API 错误映射保持不变；内部数据库异常仍由 Service 包装为 `WorkspaceErrorCode.DELETION_CLEANUP_FAILED`。
- Redis 继续只承担 arq 队列、SSE、锁/租约和短期缓存，不进入工作区删除判定链路。

## Proposed Design

在 `SqlAlchemyWorkspaceDeletionRepository` 内引入一个小型事务执行边界，用于包裹写操作：

1. 如果 `self._session.in_transaction()` 为 true：
   - 直接执行实际写操作。
   - 成功后 `commit()`，让 `deleting` 围栏或根记录删除持久化。
   - 异常时 `rollback()` 后重新抛出。
2. 如果当前 session 没有事务：
   - 保持现有 `async with self._session.begin()` 行为。

将当前 `begin_deletion()` 的主体提取为 `_begin_deletion()`，将 `delete_root()` 的主体提取为 `_delete_root()`，两个公开方法共用同一个事务边界 helper。

该方案沿用项目内已有模式：`SqlAlchemyCollectionBibliographyRepository.upsert_from_candidate()` 已对 `session.in_transaction()` 做复用、提交和回滚处理。

## Data Flow

```text
DELETE /api/v1/collections/{collection_id}
  -> get_current_user 查询用户，session 进入 autobegin
  -> ResearchWorkspaceDeletionService.delete()
  -> repository.begin_deletion()
       -> 复用既有事务，写入 deleting 和取消请求，commit
  -> 顺序轮询 running ingestion / research
       -> 查询后 rollback，释放只读事务
  -> 清理 Milvus 向量
  -> 清理 MinIO 私有对象
  -> repository.delete_root()
       -> 复用或新开事务，删除私有研究记录和工作区根记录，commit
```

## Failure Behavior

- `begin_deletion()` 找不到工作区时返回 `None`，由 Service 继续映射为 404。
- running 任务超时仍返回 `deletion_in_progress`，保留 `deleting` 状态。
- 向量、对象或数据库清理异常仍返回 `deletion_cleanup_failed`，保留可重试入口。
- 复用既有事务时任何异常都必须 rollback，避免请求级 session 进入污染状态。

## Trade-Offs

- 复用并提交既有事务会提交认证只读查询形成的空写事务。这与项目已有仓储模式一致，并保证删除围栏能在 `get_db_session` 不自动提交的前提下落库。
- 在公开方法入口直接 rollback 再新开事务也能避开嵌套错误，但它会丢弃同一请求中可能已存在的 pending 状态，且不符合已有 `collection_bibliography` 模式。
- 不改 `get_current_user` 的只读查询行为，因为该依赖服务所有受保护路由，改动面更大。

## Verification Strategy

- 新增仓储级回归测试，使用 fake `AsyncSession` 模拟 `in_transaction() == True`，并让 `begin()` 在被调用时失败，以证明修复真正避开嵌套 begin。
- 保留并运行现有工作区删除 Service 测试，确认业务编排未改变。
- 运行 API 合约测试，确认 HTTP 错误语义没有变化。
- 运行 Ruff；如测试替身或 helper 类型较复杂，补跑 Pyright。
