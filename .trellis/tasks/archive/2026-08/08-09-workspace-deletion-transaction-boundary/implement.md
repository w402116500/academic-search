# 修复工作区删除事务边界 - Implement

## Implementation Checklist

1. 在 `backend/app/infra/db/repositories/workspace_deletion.py` 中提取实际写入主体：
   - `_begin_deletion(owner_user_id, workspace_id)`
   - `_delete_root(owner_user_id, workspace_id)`
2. 新增私有 helper，统一处理写事务边界：
   - session 已在事务中：执行操作，成功 `commit()`，失败 `rollback()` 后抛出。
   - session 不在事务中：使用 `async with self._session.begin()`。
3. 让公开方法 `begin_deletion()` 和 `delete_root()` 通过该 helper 调用对应主体。
4. 新增或扩展单元测试：
   - 覆盖 `begin_deletion()` 在 `in_transaction() == True` 时不会调用 `begin()`。
   - 覆盖 `delete_root()` 在 `in_transaction() == True` 时不会调用 `begin()`。
   - 覆盖复用事务时异常会 `rollback()`。
5. 运行聚焦验证：
   - `uv run --directory backend pytest tests/unit/test_workspace_deletion_service.py tests/unit/test_workspace_api_contract.py`
   - 如果新增 repository 测试文件，则把它加入聚焦 pytest 命令。
   - `uv run --directory backend ruff check .`
6. 根据改动复杂度补跑：
   - `uv run --directory backend pyright`
   - 必要时运行更大范围 `uv run --directory backend pytest`。

## Risk Points

- 不要把外部资源清理放进数据库事务；现有两阶段删除顺序必须保持。
- 不要让 `has_running_ingestion()` 和 `has_running_research()` 并发执行；同一个请求级 `AsyncSession` 不能并发使用。
- 不要把 `ResearchEvidence` / `ResearchRun` 的显式删除改成全局级联。
- 不要读取或打印 `.env`、Cookie、LocalStorage、测试账户密码、API key 或其他凭据。

## Rollback Plan

若验证发现事务 helper 影响其他路径，回滚本任务对 `workspace_deletion.py` 和新增测试的修改；不触碰已有候选审核持久化 WIP、不触碰未跟踪父级规划任务。

## Suggested Validation Order

1. 聚焦仓储/服务/API 单测。
2. Ruff。
3. Pyright。
4. 若用户要求真实验收，再使用当前 `http://127.0.0.1:8001` API 和 `http://127.0.0.1:5173` 前端让用户重试删除；不读取浏览器凭据或 LocalStorage。

## Validation Results

- `uv run --directory backend pytest tests/unit/test_workspace_deletion_repository.py tests/unit/test_workspace_deletion_service.py tests/unit/test_workspace_api_contract.py`：11 passed。
- `uv run --directory backend pytest`：278 passed, 16 skipped。
- `uv run --directory backend ruff check .`：passed。
- `uv run --directory backend ruff format --check .`：passed。
- `uv run --directory backend pyright`：0 errors。
- `uv run --directory backend lint-imports --config ../.importlinter`：6 contracts kept。
- `uv run --directory backend python ../scripts/check_source_size.py`：passed，保留既有大文件 warning。
- `uv run --directory backend python -c "... alembic check ..."`：No new upgrade operations detected。
- `git diff --check`：passed，仅输出 Windows 换行提示。
- 已更新 `.trellis/spec/backend/database-guidelines.md`，记录受保护路由认证读取触发 SQLAlchemy autobegin 后，写仓储不得无条件 `session.begin()` 的约定。
