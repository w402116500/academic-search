# RAG 默认快速问答模式实施计划

## Checklist

- [x] 读取 backend/frontend/guides 相关 spec。
- [x] 用 CodeGraph 定位 research API、conversation payload、worker、graph、single RAG、citation helper、frontend submit/store。
- [x] 后端新增请求模式契约，兼容旧客户端。
- [x] 实现本地强复杂意图判断，灰区默认 Fast。
- [x] 实现 Fast RAG runner，复用 retriever、answer 模型、EvidenceRef 校验和 citation render。
- [x] 将 worker 执行入口按模式分派到 Fast RAG 或现有 Strict Research。
- [x] 在 retrieval trace 中记录模式、路由来源、citation/claim verification 状态与失败建议。
- [x] 前端输入区加入“快速问答 / 深度研究”模式选择，默认快速问答，并随请求发送。
- [x] 补充后端单元测试：默认 Fast 不调用 route/verifier、显式 Strict 仍走现有链路、强复杂 auto 进 Strict、Fast 证据不足不升档。
- [x] 补充前端类型/状态测试或按现有测试体系更新。
- [x] 运行受影响测试、lint/type-check；最后做全范围质量检查。
- [x] 清理旧架构空壳目录和 `__pycache__` 残留，不恢复已删除源码。
- [x] 同步后端目录规范、开发文档和本任务讨论稿中的当前 Worker/目录边界。

## Validation Results

- 2026-08-07 取消恢复补丁后：
  - `uv run --directory backend pytest`：254 passed, 15 skipped。
  - `uv run --directory backend ruff check .`：通过。
  - `uv run --directory backend ruff format --check .`：通过。
  - `uv run --directory backend pyright`：通过。
  - `uv run --directory backend lint-imports --config ../.importlinter`：通过。
  - `uv run --directory backend python ../scripts/check_source_size.py`：通过；提示既有大文件责任审查 warning。
  - `corepack pnpm typecheck`：通过。
  - `corepack pnpm lint`：通过。
  - `corepack pnpm format:check`：通过。
  - `corepack pnpm test:unit`：19 passed。
  - 数据库检查：`research_runs` 中 `queued` / `running` 非终态数量为 0。
- 2026-08-07 架构清理补充：
  - 旧空壳目录扫描确认 6 个候选：`.codex/skills`、`backend/app/db`、
    `backend/app/modules/collections`、`backend/app/modules/fulltext`、
    `backend/app/modules/ingestion`、`backend/app/modules/workflow`。
  - 清理脚本删除前校验目标均在仓库内，且目录下无源码文件、只含空目录或
    `__pycache__/.pyc` 缓存。
  - `AGENT.md` 已检查，项目架构不变量仍指向当前 `infra/db`、`research`、
    `search`、`documents`、`rag` 和 `agents` 所有者，无需修改。
- `uv run --directory backend pytest`：248 passed, 15 skipped。
- `uv run --directory backend ruff check .`：通过。
- `uv run --directory backend ruff format --check .`：通过。
- `uv run --directory backend pyright`：通过。
- `uv run --directory backend lint-imports --config ../.importlinter`：通过。
- `uv run --directory backend python ../scripts/check_source_size.py`：通过；提示既有大文件责任审查 warning。
- `corepack pnpm typecheck`：通过。
- `corepack pnpm lint`：通过。
- `corepack pnpm format:check`：通过。
- `corepack pnpm test:unit`：19 passed。
- `corepack pnpm api:generate`：通过并更新 OpenAPI 产物；`api:check` 在未提交生成文件时按预期因 `git diff --exit-code` 失败。

## Validation Plan

优先运行：

```powershell
cd E:\myproject\academic-search
python -m pytest backend/tests/unit/test_research_graph.py
python -m pytest backend/tests/unit/test_research_conversations.py
python -m pytest backend/tests/unit/test_research_retrieval.py
```

根据项目脚本补充：

```powershell
python -m pytest backend/tests/unit
ruff check backend
pyright backend
```

前端根据 package 脚本运行相关 type/test：

```powershell
cd E:\myproject\academic-search\frontend
npm test -- --run
npm run type-check
```

实际命令以仓库脚本为准；如果命令不存在，记录未执行原因并使用等价检查。

## Risky Files

- `backend/app/modules/agents/graph.py`
- `backend/app/modules/agents/nodes/single_rag.py`
- `backend/app/workers/research.py`
- `backend/app/api/routers/research_conversations.py`
- `backend/app/infra/db/repositories/research_conversations.py`
- `frontend/src/views/ResearchChatView.vue`

## Rollback Points

- Fast RAG runner 应作为新增或隔离路径实现，避免破坏现有 Strict Research。
- 前端模式字段应向后兼容，旧请求不传字段也能运行。
- 若测试显示引用协议风险，优先回退 Fast RAG 成功路径，不放宽引用校验。
