# 修复工作区删除事务边界

## Goal

修复 `DELETE /api/v1/collections/{collection_id}` 在真实登录请求下无法继续删除工作区的问题。用户应能对首次删除或已处于 `deleting` 状态的工作区重复执行删除；如果后台任务尚未停止或外部资源清理失败，仍按既有契约返回可恢复错误，而不是因为数据库会话事务状态提前失败。

## Background

- 真实删除请求先经过 `get_current_user`，该依赖会通过 `SqlAlchemyUserRepository.find_active_by_id()` 查询用户，触发请求级 `AsyncSession` 的 SQLAlchemy autobegin 事务。证据：`backend/app/api/deps/auth.py:51`、`backend/app/infra/db/repositories/users.py:55`。
- 删除路由随后复用同一个请求级 `AsyncSession` 组装 `ResearchWorkspaceDeletionService`。证据：`backend/app/api/routers/collections.py:139`、`backend/app/api/deps/services.py:87`。
- `SqlAlchemyWorkspaceDeletionRepository.begin_deletion()` 和 `delete_root()` 目前无条件执行 `async with self._session.begin()`。当认证读已经让 session 处于事务中时，SQLAlchemy 抛出 `InvalidRequestError: A transaction is already begun on this Session.`。证据：`backend/app/infra/db/repositories/workspace_deletion.py:32`、`backend/app/infra/db/repositories/workspace_deletion.py:98`。
- 项目数据库规范明确：`get_db_session` 只提供请求级 session，不替业务自动提交或回滚；仓储方法拥有自己的事务边界。证据：`.trellis/spec/backend/database-guidelines.md`。
- 工作区删除契约明确：PostgreSQL 的工作区状态是删除围栏；Redis 仅承担队列、事件、锁和短期缓存职责，不能成为删除能否继续的持久依据。证据：`.trellis/spec/backend/research-run-lifecycle.md` 的“工作区永久删除围栏”。

## Requirements

- R1. `SqlAlchemyWorkspaceDeletionRepository.begin_deletion()` 必须兼容进入方法前已经 autobegin 的请求级 `AsyncSession`，不得因认证或其他只读依赖已经查库而抛出嵌套事务错误。
- R2. `SqlAlchemyWorkspaceDeletionRepository.delete_root()` 必须具备同样的事务边界兼容能力，避免第二阶段数据库根记录删除再次踩中同类问题。
- R3. 修复后仍必须保持工作区删除围栏契约：先持久化 `deleting` 和取消请求，再等待后台任务停止，清理向量和私有对象，最后删除数据库私有记录和工作区根记录。
- R4. 修复后仍必须保持可重试语义：外部清理失败或后台任务未终态时保留 `deleting` 状态；重复 DELETE 从持久快照继续。
- R5. 修复不得改变 Redis 职责边界，不得新增旧 Redis key 清理、旧候选快照兼容、前端 UI 文案或凭据读取逻辑。
- R6. 新增回归测试必须覆盖“同一个 session 已经处于事务中时调用删除仓储写方法不会再调用 `begin()` 并会完成提交/回滚”的场景。

## Acceptance Criteria

- [x] AC1. 在模拟已 autobegin 的 session 下，`begin_deletion()` 不调用 `session.begin()`，正常返回 `WorkspaceDeletionSnapshot` 并提交事务。
- [x] AC2. 在模拟已 autobegin 的 session 下，`delete_root()` 不调用 `session.begin()`，正常执行私有记录清理和根记录删除并提交事务。
- [x] AC3. 若复用既有事务执行期间发生异常，仓储会 rollback 并继续向上抛出，使 Service 保持现有 `deletion_cleanup_failed` 包装语义。
- [x] AC4. 既有 Service 编排测试仍通过：外部清理失败不删除根记录，等待超时返回 `deletion_in_progress`，运行状态查询保持顺序执行。
- [x] AC5. 相关后端质量门通过：至少运行聚焦 pytest、Ruff；若改动触达类型边界或测试替身较复杂，再运行 Pyright。

## Out Of Scope

- 不清理 Redis、MinIO、Milvus 或真实数据库里的既有开发数据。
- 不修改前端删除弹窗、提示文案或路由跳转行为。
- 不改变 `get_db_session` 的请求级生命周期，也不把事务管理上移到 FastAPI 路由层。
- 不兼容候选审核旧 Redis 快照；该范围属于已有候选审核持久化任务。
- 不 commit、push、archive 或 finish-work，除非用户后续明确要求。

## Open Questions

无阻塞开放问题。建议按本规划进入实施前，由用户确认一次。
