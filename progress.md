# Progress Log

## Session: 2026-07-31

### Phase 1: 需求与边界确认

- **Status:** complete
- **Started:** 2026-07-31
- Actions taken:
  - 阅读产品方向、文献检索、结果引用、RAG、会话治理、数据库和前端交互讨论稿。
  - 核对后端现有工作区 API、Provider 处理链、全文准入服务和 RAG 入库 Worker。
  - 统一讨论稿中工作区创建时机与 Worker 实现状态的矛盾描述。
  - 将“入口到文献结果”的后端闭环拆成工作流契约、意图分析、多源检索、全文准入、端到端验证六个阶段。
  - 记录状态字段使用英文稳定值、同时提供中文注释和展示元数据的实现约束。
- Files created/modified:
  - `task_plan.md`（创建）
  - `findings.md`（创建）
  - `progress.md`（创建）
  - `docs/02-frontend-design-system.md`
  - `docs/03-literature-search-and-discovery-discussion.md`
  - `docs/04-literature-results-and-citation-discussion.md`
  - `docs/07-frontend-experience-discussion.md`
  - `docs/09-database-design-discussion.md`

### Phase 2: 工作流契约与状态基础

- **Status:** complete
- Actions taken:
  - 阅读现有工作区模型、工作区服务、Alembic 迁移和单元测试模式。
  - 确认 `research_collections.status` 仅表示生命周期，可安全新增独立工作流阶段。
  - 确认本阶段新增 `research_plans` 与轻量 `search_runs`，候选详情仍不写入长期 PostgreSQL。
- Files created/modified:
  - `task_plan.md`
  - `progress.md`

- Completed implementation:
  - 在 `research_collections` 中新增独立 `workflow_stage`，并通过阶段状态机集中限制合法转换。
  - 新增 `research_plans` 与 `search_runs` 模型、Pydantic 契约、Redis 会话键约定与阶段服务。
  - 新增 Alembic migrations `e2a7c4b9d113`、`f41c8e7b2a06`，并将本地开发库升级至 head。
  - 扩展工作区 API 响应，返回稳定英文阶段值及中文展示标签、说明。
  - 新增流程契约、权限、阶段跳跃、重复事件、年份范围和 Redis 键测试。

### Phase 3: 创建工作区与意图分析

- **Status:** complete
- Completed implementation:
  - 新增首页提交、读取计划、重新生成和确认计划 API；所有接口均复用工作区所有权校验。
  - 提交时创建活动工作区、首版 `generating` 计划和确定的 arq Job ID；队列失败会明确写为 `failed`，不会返回伪成功。
  - 新增 OpenAI 兼容 JSON mode 意图分析器，真实模型输出再经过 `ResearchPlanDraft` Pydantic 校验。
  - 每个候选方向都有独立检索表达式；确认后只保留选中方向的查询，并固化用户选择的时间与语言范围。
  - 新增独立的 `app.workers.workflow.WorkerSettings`，并将 Redis 连接配置抽为 Worker/API 共用模块。
  - 使用已配置模型做真实无副作用调用，成功返回 3 个方向和各自查询计划；Redis arq 连接验证通过。

## Test Results

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| 文档格式检查 | `git diff --check` | 无空白错误 | 通过 | ✓ |
| 工作流单元测试 | `uv run pytest tests/unit` | 通过 | 78 passed | ✓ |
| 后端静态检查 | Ruff、Pyright | 无错误 | 通过 | ✓ |
| 迁移一致性 | `uv run alembic check` | 无待生成迁移 | 通过 | ✓ |
| 本地数据库迁移 | `uv run alembic upgrade head` | 升级到最新 revision | `f41c8e7b2a06` | ✓ |
| 真实意图分析 | 已配置 OpenAI 兼容模型 | 返回可确认计划草稿 | 返回 3 个方向与方向查询计划 | ✓ |
| Redis 队列 | arq `PING` | Redis 可连通 | 通过 | ✓ |
| API 存活 | `GET /healthz` | HTTP 200 | `{"status":"ok"}` | ✓ |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-07-31 | 规划文件不存在 | 1 | 创建 `task_plan.md`、`findings.md`、`progress.md` |
| 2026-07-31 | `alembic check` 报告三个唯一索引与 ORM 唯一约束不一致 | 1 | 新增校正迁移 `f41c8e7b2a06_align_workflow_unique_constraints` 并应用 |
| 2026-07-31 | PowerShell 单行 `async def` 真实调用脚本语法错误 | 1 | 改用 here-string 管道执行多行 Python 脚本 |
| 2026-07-31 | 首次真实 JSON mode 输出使用自定义包装和字段 | 1 | 在提示词中明确顶层与嵌套 JSON 形状，并将 LangChain 解析异常分类为结构错误 |

## Session: 2026-08-01

### Phase 4: 多源检索任务与进度

- **Status:** complete
- Completed implementation:
  - 增加 `SearchRunService`，仅允许已确认计划创建检索运行，并用唯一活动索引防止重复提交。
  - 增加 arq `run_search` Worker，复用 Provider Registry、来源网络路由和限速配置。
  - 增加并发来源执行、统一规整/去重/初筛、可选 DOI 题录补全，以及来源级错误隔离。
  - 增加 Redis JSON 快照与 Stream 事件，提供候选读取、SSE 断线恢复和失败重试 API。
  - 终态 SSE 连接在发送初始快照后立即结束，不再无意义阻塞等待新事件。
  - 增加 Redis 会话单元测试和真实 PostgreSQL/Redis 多源运行验收测试。
  - 修正 Milvus upsert 后的最终一致性窗口，入库向量 flush 后才返回成功。
- Verification:
  - `uv run pytest tests`：101 passed、4 skipped；`uv run pytest tests/unit`：98 passed。
  - `uv run ruff check app tests`、`uv run ruff format --check app tests`、`uv run pyright`：通过。
  - `uv run alembic check`：无待生成迁移。
  - 真实多源运行：四个启用来源成功，75 条原始候选规整去重后 57 条，状态 `completed`；临时数据已清理。

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | Phase 1 至 Phase 4 已完成，下一阶段是全文准入与入库任务 API |
| Where am I going? | 从 Redis 候选中获取并核验全文，通过准入后投递 RAG 入库任务 |
| What's the goal? | 打通研究要求到统一文献结果的后端闭环 |
| What have I learned? | Provider、准入服务和 RAG 入库 Worker 已存在；真实模型需要明确 JSON 结构提示才能稳定通过契约 |
| What have I done? | 完成工作流状态、研究计划 API、意图分析 Worker、多源检索 Worker、Redis/SSE 进度和真实多源验证 |
