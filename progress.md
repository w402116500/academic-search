# Progress Log

## Session: 2026-08-02 RAG 研究会话完整实施

- 恢复并审计 RAG 后端、前端 API 客户端和规划文件。确认后端代码、迁移和两组离线单测已经存在；计划文件此前未反映实际完成状态，已新增实施对齐清单。
- 基线验证尝试：`pnpm run typecheck` 因当前终端提供 Node `v24.14.0`、pnpm `11.9.0` 与项目要求 Node `>=20.19 <21`、pnpm `>=10.34.5 <11` 不兼容而未执行。后续将使用项目指定版本完成检查；这不是业务代码错误。
- 使用 `E:\nodejs\node.exe`（v20.19.6）复验前端类型、ESLint、Prettier 和 Vite build，均通过；后端完整验证为 `126 passed, 8 skipped`，Ruff、Pyright 和 Alembic check 通过。
- 只读盘点现有活动集合：3 个集合均没有完成入库的当前文档版本和 L3 向量。后续真实 RAG 验收将使用专属临时资源并按 UUID 精确清理，不触碰现有数据。
- 新增 `tests/integration/test_live_research_e2e.py`：默认跳过；启用后以随机 UUID 构造隔离的已准入/current 文档版本，真实调用 Qwen embedding、Milvus、DeepSeek、LangGraph checkpoint 和 Redis Stream。第三次真实运行通过，耗时约 146 秒，回答引用 2 个当前 L3 片段并写入 5 个事件；临时数据、向量、事件与 checkpoint 均已清理。
- 修复真实验证暴露的运行问题：Windows checkpoint 事件循环策略、DeepSeek JSON mode 提示词、证据不足的空引用契约，以及研究运行的 ORM 删除级联。相关离线图测试为 `5 passed`。

## Session: 2026-08-02 真实功能链路验收

- **Status:** 已完成本轮真实功能验收；Phase 8.5 的故障恢复专项基线仍待后续补齐。
- 后端基础回归：`uv run pytest tests -q` 为 `127 passed, 9 skipped`；Ruff、格式检查、Pyright 与 `alembic check` 均通过。
- 修正 live 异步测试的事件循环范围：SQLAlchemy/asyncpg 全局连接池在 pytest 每用例独立事件循环时会复用绑定旧循环的连接，导致后续测试报 `Future attached to a different loop`。在 `pyproject.toml` 固定 `asyncio_default_test_loop_scope = "session"` 后，同进程连续执行全文、准入、构建和入库验收恢复稳定。
- 真实 API + workflow Worker 验收：临时账号通过 `POST /auth/register` 注册，提交研究要求后 DeepSeek 生成 3 个方向；确认计划后由独立 arq Worker 执行检索。该次 OpenAlex、Crossref、arXiv 均完成，最终得到 51 条候选、50 条纳入候选、25 条题录补全；候选快照经 API 成功读取。
- 真实全文/入库验收：从 arXiv 下载 `Attention Is All You Need` 的 2,215,244 字节 PDF，写入 MinIO，完成严格准入、集合构建、解析分块和硅基流动 Qwen embedding。运行完成后 PostgreSQL 分块为 L1=4、L2=13、L3=47，Milvus 为 47 条 1024 维 L3 向量，工作区进入 `researching`。
- 真实 API + research Worker 验收：临时已索引集合通过会话 API 创建会话和问题；研究 Worker 从专用 arq 队列领取任务后，Qwen 查询嵌入、Milvus 受限检索和 DeepSeek 回答均成功。API 返回 `completed`、2 条可回链证据和答案；Redis Stream 有 5 条公开事件，SSE 返回初始快照。
- 真实多源测试本轮也验证了单源故障隔离：Crossref、arXiv 成功时，OpenAlex 的本地代理连接失败及 Semantic Scholar 兼容网关超时会让运行进入 `partial_failed`，不会伪装为全量成功。
- 所有新增临时资源均按 UUID 清理；复查 PostgreSQL 中两次临时用户、工作区、研究运行均为 0，Milvus 中临时集合向量为 0。为验收临时启动的 workflow、ingestion、research Worker 进程已停止；既有 FastAPI、Vite 与 Docker 状态服务未停止。

## Session: 2026-08-02

### Phase 8: RAG 研究会话后端规划

- **Status:** planned
- Actions taken:
  - 恢复并核对既有规划文件、真实前端联调结果、RAG 讨论稿、会话治理稿、数据库设计稿和前端交互稿。
  - 将已通过真实浏览器回归的 Phase 7 标记为完成，避免 Phase 8 仍错误依赖前端主流程验证。
  - 将原本单一且被阻塞的“RAG 对话前端接入”拆为 Phase 8 后端五个实施点和 Phase 9 前端接入。
  - 明确第一版先完成可审计的 `single_rag`，复杂问题的 Plan-and-Solve、受限 ReAct 与证据核验在检索、引用和恢复能力真实验收后实施。
  - 明确 Milvus 只作向量召回索引，PostgreSQL 是文档版本、原文定位、权限和最终证据的业务真相来源。
- Files created/modified:
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### Phase 8.1: 会话、运行与接口契约

- **Status:** in_progress
- Actions taken:
  - 确认既有研究领域模型已经预留会话、消息、研究运行与证据表；后续实现优先复用这些实体，避免重复创建平行数据模型。

## Error Log

| Timestamp  | Error                                                   | Attempt | Resolution                                                                     |
| ---------- | ------------------------------------------------------- | ------- | ------------------------------------------------------------------------------ |
| 2026-08-02 | 根目录 `.env.sample` 不存在，环境模板读取失败           | 1       | 改为通过文件列表定位实际模板，不重复假定文件名。                               |
| 2026-08-02 | Phase 8.1 首轮 Ruff 检查发现格式问题                    | 1       | 统一格式并避免响应字段使用 Pydantic 保留的 `model_config` 名称后复查。         |
| 2026-08-02 | 字段重命名补丁命中错误文件，未写入                      | 1       | 根据实际定义位置在研究 API 契约中修复，不重复使用原补丁。                      |
| 2026-08-02 | 初次探查 `AsyncPostgresSaver` 时假定它具有 `__aenter__` | 1       | 确认 `from_conn_string()` 返回异步上下文管理器，按官方 `async with` 方式集成。 |
| 2026-08-02 | 前端端到端测试文件名假定错误                            | 1       | 先列出实际测试目录再读取，不重复使用不存在路径。                               |

## Session: 2026-07-31

### Phase 1: 需求与边界确认

- **Status:** complete

## 2026-08-04 — 意图分析失败排查启动

- 已检查用户截图并确认失败位于意图分析/检索计划生成阶段，而非 RAG 重排或回答阶段。
- 当前排查路径：核对前端失败按钮是否触发真正的重生成 API；检查服务端模型设置和 Worker 异常；以不持久化探针验证意图分析模型当前是否可用；随后实施最小修复并执行真实浏览器回归。
- 设置存在性检查确认当前选择 DeepSeek，Base URL、模型和密钥均已配置，`workflow_intent_timeout_seconds=45`；未读取或输出凭据。
- 真实意图分析探针第一次因把 `async def` 放入单行 `python -c` 导致 Python `SyntaxError`，未执行模型调用；已改为通过 PowerShell here-string 把脚本传给 Python 标准输入。
- 改正后真实探针通过（19.6 秒）：对截图的同一问题返回 3 个方向和 3 份查询计划，模型为 `deepseek-v4-flash`。因此截图是历史失败计划，恢复按钮必须投递重生成而非仅 refetch。
- 前端首次 `vue-tsc --noEmit` 只发现新增失败计划浏览器夹具的 TypeScript 窄类型推断：由失败对象推断出的 `error_code` 与 `selected_direction_id` 不能接受恢复态的 `null`。将基准夹具显式标注为后端 `ResearchPlan` 契约后重跑；不涉及产品运行逻辑。
- 第二次类型检查仍因对象展开将失败/重新生成夹具的 `status` 放宽为 `string` 而失败；改为直接将两份夹具标注为 `ResearchPlan`，这是同一测试数据类型问题的精确收口。
- 实施完成：`PlanReviewView.vue` 的失败操作由 `refetch()` 改为 `regeneratePlan()` mutation。成功时缓存切换到 API 返回的 `generating` 新版本并继续既有轮询；请求失败时在同一失败面板显示明确错误。
- 验收完成：`vue-tsc --noEmit`、ESLint、定向 Playwright（1 passed）、全量 Playwright（9 passed）和生产构建均通过。`git diff --check` 无空白错误；未改动或回退工作区既有脏文件。

## 2026-08-04 — 真实浏览器全链路验收启动

- 用户指定真实前端输入为“我想研究睡眠质量与心理健康之间的关系”。已读取 Playwright CLI 技能要求，确认 `E:\nodejs\npx.cmd` 与本机 CLI 包装脚本均可用；将以独立临时账号测试并清理资源。
- 首次通过 `bash C:/.../playwright_cli.sh` 调用包装器失败：系统解析到 WSL 的 `C:\Windows\System32\bash.exe`，不能访问 Windows 风格路径，脚本没有执行。下一次将定位 Git Bash 或改用包装器等价的 Windows `npx.cmd --package @playwright/cli playwright-cli` 调用，不重复同一命令。
- Playwright CLI 已用 Windows `npx.cmd` 成功打开真实前端并从登录页进入注册页；但独立 `snapshot` 调用持续等待且在 124 秒后超时。点击命令本身已生成最新 snapshot 文件，因此后续将读取该文件取得新引用，不重复这个超时命令，也不将 CLI 轮询延迟误判为前端失败。
- 注册提交返回 HTTP 422，页面准确显示“输入不符合格式要求，请检查后重试”。当前临时数据没有写入成功；下一步读取后端注册契约并以新数据执行，不重复同一请求。
- 已通过 Playwright 网络响应核对 422：`example.test` 是保留域名，`EmailStr` 正确拒绝。改用非保留邮箱域的唯一临时数据继续；该次 422 未创建账号或工作区。
- 真实浏览器注册成功（201）并从首页提交指定研究要求；前端进入新工作区、显示“正在理解这项研究”，并持续轮询。最新计划 API 保持 `generating`，没有错误落库；进入 Workflow Worker/Redis 队列诊断，不重复创建工作区。

## 2026-08-04 — 本地开发进程重启

- 已停止并重新启动前端 Vite 与 workflow、ingestion、research 三类 ARQ Worker；启动后每类 Worker 仅保留一组正常的包装器与 Python 子进程，前端 `127.0.0.1:5173` 返回 HTTP 200。
- `127.0.0.1:8000` 的 Uvicorn 监听由宿主机无法解析的重载监督进程持有，不能安全按普通 PID 终止；通过仅刷新 `backend/app/main.py` 的修改时间触发既有 `--reload` 重载，未改动代码内容，`/healthz` 返回 HTTP 200。
- Docker Compose 的 PostgreSQL、Redis、Milvus、MinIO 与 etcd 未重启，已有本地数据保持不变。
- 首次批量终止命令因 PowerShell 嵌套 `$_` 变量未匹配旧 Worker，随后产生重复消费者；已完整停止所有明确 Worker 命令行进程并按单实例重新启动。另一次批量启动/验证命令受本机策略拒绝，改为逐进程启动与单独健康验证。
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

| Test           | Input                         | Expected            | Actual                      | Status |
| -------------- | ----------------------------- | ------------------- | --------------------------- | ------ |
| 文档格式检查   | `git diff --check`            | 无空白错误          | 通过                        | ✓      |
| 工作流单元测试 | `uv run pytest tests/unit`    | 通过                | 78 passed                   | ✓      |
| 后端静态检查   | Ruff、Pyright                 | 无错误              | 通过                        | ✓      |
| 迁移一致性     | `uv run alembic check`        | 无待生成迁移        | 通过                        | ✓      |
| 本地数据库迁移 | `uv run alembic upgrade head` | 升级到最新 revision | `f41c8e7b2a06`              | ✓      |
| 真实意图分析   | 已配置 OpenAI 兼容模型        | 返回可确认计划草稿  | 返回 3 个方向与方向查询计划 | ✓      |
| Redis 队列     | arq `PING`                    | Redis 可连通        | 通过                        | ✓      |
| API 存活       | `GET /healthz`                | HTTP 200            | `{"status":"ok"}`           | ✓      |

## Error Log

| Timestamp  | Error                                                 | Attempt | Resolution                                                                |
| ---------- | ----------------------------------------------------- | ------- | ------------------------------------------------------------------------- |
| 2026-07-31 | 规划文件不存在                                        | 1       | 创建 `task_plan.md`、`findings.md`、`progress.md`                         |
| 2026-07-31 | `alembic check` 报告三个唯一索引与 ORM 唯一约束不一致 | 1       | 新增校正迁移 `f41c8e7b2a06_align_workflow_unique_constraints` 并应用      |
| 2026-07-31 | PowerShell 单行 `async def` 真实调用脚本语法错误      | 1       | 改用 here-string 管道执行多行 Python 脚本                                 |
| 2026-07-31 | 首次真实 JSON mode 输出使用自定义包装和字段           | 1       | 在提示词中明确顶层与嵌套 JSON 形状，并将 LangChain 解析异常分类为结构错误 |
| 2026-08-01 | 状态约束迁移按简写名删除失败                          | 1       | 历史表使用最终约束名；改用 `op.f("ck_ingestion_runs_status")` 后成功应用  |
| 2026-08-01 | 本地 COMMENT 同步使用绑定参数失败                     | 1       | PostgreSQL DDL 不支持该参数位置；改为固定注释文案后成功同步               |

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

### Phase 5: 全文准入与入库任务 API

- **Status:** complete
- 已确认的实现边界：搜索候选与短期全文状态只保留在 Redis；只有 DOI 题录、可用 PDF 和访问权限均核验完成后，才能复用 `ResearchCollectionAdmissionService` 写入长期表。
- 已完成候选全文获取、轮询和重试 API；候选、题录、PDF URL 与暂存对象键都只从服务端 Redis 搜索会话读取，前端无法伪造。
- 已完成全文准入 API：`available` 全文通过既有严格准入服务转正，并创建 `IngestionRun(status=pending)`；Worker 的 `claim()` 不会领取此状态。
- 已完成集合文献列表、确认构建、失败运行重试和待确认文献移出 API。构建先提交 `queued` 状态，再逐篇投递 arq；队列失败仅标记对应运行 `failed/ingestion_queue_unavailable`。移出操作归档关联并取消运行，不立即删除正式对象。
- 已增加 `CandidateFulltextService` 和集合构建服务的离线测试，以及真实 PostgreSQL、MinIO 准入和真实 PostgreSQL 构建集成测试；测试数据均已清理。
- 已应用并校验 `a6d2e9f7c418` 迁移。修复了历史 check constraint 命名约定导致的迁移删除失败，并同步数据库列注释。

- 已增加可重复执行的真实端到端入库测试：使用 arXiv 开放 PDF 验证全文暂存、准入转正、构建投递、Worker 解析分块、Embedding、Milvus 写入和工作区状态推进。
- 修复 `SqlAlchemyIngestionRepository` 的事务边界：`AsyncSession` 的只读查询会隐式开启事务；仓储层现在先将 ORM 结果转为值对象，再结束该只读事务，避免后续写事务抛出“transaction is already begun”。

## Phase 5 Verification

- 阶段五离线测试：`CandidateFulltextService` 与集合构建服务共 `9 passed`，覆盖权限、幂等、重试、移出和状态汇总。
- 真实 PostgreSQL 构建测试：`pending -> queued`、写入 arq Job ID、集合进入构建中，`1 passed`，临时数据已清理。
- 真实 PostgreSQL + MinIO 准入测试：暂存对象转正、创建 `pending` 运行、对象补偿清理，`1 passed`，临时数据已清理。
- 真实端到端入库测试：`RUN_LIVE_INGESTION_E2E_TESTS=1 uv run pytest tests/integration/test_live_ingestion_e2e.py -m live -s` 通过；论文为 `Attention Is All You Need`（DOI：`10.48550/arXiv.1706.03762`），真实 PDF 下载大小为 2,215,244 bytes。
- 真实端到端结果：入库状态 `completed`，向量维度 `1024`；PostgreSQL 分块数为 L1=`4`、L2=`13`、L3=`47`，Milvus L3 向量数为 `47`，工作区阶段为 `researching`。
- 真实测试清理：临时 User、ResearchCollection、IngestionRun、MinIO 暂存/正式对象以及指定 `ingestion_run_id` 的 Milvus 向量均已精确删除。
- 完整后端回归：`112 passed, 6 skipped`；`uv run ruff check app tests`、`uv run ruff format --check app tests` 与 `uv run pyright` 均通过。

## 5-Question Reboot Check

| Question             | Answer                                                                                                                       |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Where am I?          | Phase 1 至 Phase 5 已完成，下一阶段是端到端验证与前端接入准备                                                                |
| Where am I going?    | 以 Vue 原型为基准整理 OpenAPI 契约、恢复与隔离验证，再实现基于已完成向量索引的 RAG 检索与研究对话                            |
| What's the goal?     | 打通从研究要求到可研究文献集合的后端闭环，并为研究问答提供可信证据基础                                                       |
| What have I learned? | Provider、准入服务和 RAG 入库 Worker 已存在；真实模型必须有明确 JSON 提示，SQLAlchemy 异步会话也必须显式管理只读后的事务边界 |
| What have I done?    | 完成工作流、计划、检索、全文准入与集合构建 API，并以真实 PDF、Embedding 和 Milvus 验证入库完成后工作区可进入研究阶段         |

### Phase 6: 端到端验证与前端接入准备

- **Status:** complete
- 已按前端工作区切换器需求将 `GET /api/v1/collections` 改为关键词与游标分页接口；搜索匹配工作区名称，以及研究阶段的英文值、中文标签和中文说明。
- 分页使用 `updated_at + id` 稳定排序键，客户端只回传不透明 `next_cursor`；损坏游标返回稳定的 `workspace_invalid_cursor` 错误。
- 新增工作区分页服务测试和 OpenAPI 契约测试，防止接口退回一次性数组或丢失 `q/cursor/limit` 参数。
- 新增离线 FastAPI 流程契约测试，覆盖提交研究要求、刷新计划、确认时间/语言范围和创建检索运行；测试验证全过程复用同一 `workspace_id` 与 `research_plan_id`。
- 阶段六当前验证：完整测试 `116 passed, 6 skipped`；新增 API 流程、分页服务和 OpenAPI 测试均通过，Ruff、格式检查和 Pyright 均通过。
- 新增真实 API 状态恢复验收：页面刷新可读取工作区、已确认计划、部分失败检索运行和 Redis 候选；终态 SSE 返回初始快照，运行中 SSE 可用 `Last-Event-ID` 补回进度事件。
- 真实 API 验收验证了另一账号访问工作区、计划、检索运行和候选均返回 404；Redis 候选过期返回 410 并将数据库运行标记为 `expired`；部分失败重试创建 `attempt_no=2` 的 queued 运行。
- 外部 Embedding 冒烟测试改为 `RUN_LIVE_EMBEDDING_TESTS=1` 显式开启，普通回归不再受外部网络状态影响。
- 修复候选全文状态 API 未处理底层 `SearchRunError` 的问题；跨账号访问现在统一返回 404，不会暴露为 500。
- 阶段六真实命令：`RUN_LIVE_API_STATE_RECOVERY_TESTS=1 uv run pytest tests/integration/test_live_api_state_recovery.py -m live -s`，结果 `1 passed`；完整回归 `118 passed, 8 skipped`。

## Session: 2026-08-01

### Phase 7: Vue 前端主流程实施规划

- **Status:** planned
- **Actions taken:**
  - 对照 `frontend/` 当前目录、静态原型和前端交互讨论稿，确认 Vue 页面尚未实现，但前端依赖和测试工具已配置。
  - 确认首版前端范围为认证、研究入口、意图确认、检索进度、文献结果、论文详情、全文准入和集合构建。
  - 确认 RAG 研究对话暂不实现真实回答，等待后端研究会话、检索和证据 API。
  - 将前端实施拆为 Phase 7，并将 RAG 对话前端接入单独列为 Phase 8。
- **Files created/modified:**
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### Phase 7: 前端恢复与题录接口补齐

- **Status:** in_progress
- **Actions taken:**
  - 恢复上一会话的实施上下文并核对任务计划、前端路由、API 契约和静态原型要求。
  - 修复结果页从侧栏进入时不会写入当前搜索运行 ID 的问题，避免候选查询永久停留在未启用状态。
  - 新增按工作区阶段选择目标路由的单一映射，工作区切换器不再一律跳转到计划页。
  - 发现候选题录的 CSL/BibTeX 格式化器没有 HTTP API；后续实现将补齐服务端渲染，再替换前端的非正式“标题 + DOI”复制。
- **Files created/modified:**
  - `frontend/src/router/workspace-route.ts`
  - `frontend/src/components/AppHeader.vue`
  - `frontend/src/views/ResultsView.vue`
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### Phase 7: 正式引用与前端自动化验收

- **Status:** in_progress
- **Completed implementation:**
  - 新增共享候选会话读取服务，全文获取与正式引用均从当前用户拥有的 Redis 候选快照读取，前端不能提交或覆盖题录字段。
  - 新增候选正式引用 API，支持 GB/T 7714-2015、APA 7、MLA 9、Chicago author-date 与 BibTeX；只有 `ready` 题录可渲染。
  - 结果页支持 GB/T 快捷复制；论文详情页支持多格式选择、服务端预览和复制。题录未完成核验时明确显示不可生成，而不是拼接标题与 DOI。
  - 工作区跳转按服务端阶段路由；结果页无 `run` 参数进入时恢复当前运行 ID。
  - 将研究范围构造提取为可测函数，覆盖近五年、未来年份和空语言集合；新增工作区路由映射单测与认证 Playwright 用例。
- **Verification:**
  - 后端完整回归：`121 passed, 8 skipped`；Ruff、格式化、Pyright 与 `alembic check` 全部通过。
  - 真实 PostgreSQL/Redis API 验收：候选会话恢复、跨账号 404、SSE 断线恢复、重试、过期及 APA 引用渲染均通过，测试数据已清理。
  - 前端：`vue-tsc`、ESLint、Prettier、Vite build、Vitest（4 个断言）与 Playwright（2 个认证用例）均通过。
- **Remaining:**
  - 仍需通过浏览器创建临时账号后，完成包含真实模型、Provider 和 Worker 的整条 UI 主路径验收；该项受外部模型和文献源配额影响，不作为普通回归测试执行。

### Phase 7: Vue 前端主流程实施

- **Status:** in_progress
- **Completed implementation:**
  - 创建 Vite 入口、Vue Router、TanStack Query、Pinia 认证状态、统一 FastAPI 客户端和完整的 TypeScript API 契约。
  - 完成登录、注册、令牌恢复和鉴权路由守卫；未登录用户会被送到登录页，不显示伪造的账号菜单。
  - 完成研究入口、计划生成轮询、方向/时间/语言确认，以及确认后创建检索运行的连续流程。
  - 完成检索进度页：使用携带 Bearer Token 的 `fetch` 流读取 SSE，展示来源、阶段、失败和重试。
  - 完成候选结果表、论文详情、引用摘要复制、开放全文获取/轮询、准入和研究集合构建页面。
  - 完成工作区关键词搜索、游标分页追加和左侧栏底部折叠按钮；研究对话入口保持锁定，说明后端 RAG API 尚未实现。
- **Verification:**
  - 使用 Node `20.19.6` 直接运行 `vue-tsc --noEmit`、ESLint 与 Vite production build，均通过。
  - 通过 Playwright 打开真实 Vite 登录页并截取 `output/playwright/login-final.png`，确认页面标题、表单、无障碍标签与视觉布局正常。
- **Issue:**
  - 当前终端的 pnpm `11.9.0` 依赖 Node `>=22.13`，不能用于固定 Node `20.19.6` 的项目；未修改锁文件，改用 Node 20 直接调用已安装工具完成本轮验证。
- **Files created/modified:**
  - `frontend/index.html`
  - `frontend/public/favicon.svg`
  - `frontend/src/`
  - `frontend/eslint.config.mjs`
  - `README.md`
  - `docs/02-frontend-design-system.md`
  - `docs/07-frontend-experience-discussion.md`
  - `docs/10-frontend-interaction-flow-discussion.md`
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### Phase 7.1: 原型交互流程对齐

- **Status:** in_progress
- **Actions taken:**
  - 对照 `prototypes/frontend/01-research-entry.html`、`03-literature-results.html`、`04-research-workspace.html` 与现有 Vue 路由。
  - 确认现有实现将原型的连续画布拆为计划、检索和结果三个平级页面，且侧栏允许跳过当前阶段。
  - 确认本轮只重组前端状态与页面层级，保留现有 FastAPI、TanStack Query、SSE、全文与集合构建接口。
  - 新增 `ResearchRunnerView`：提交研究要求后保持无侧栏的连续画布，并由服务端工作区阶段和运行标识决定展示计划确认或检索进度。
  - 将旧的 `/plan`、`/search` 路径改为兼容性重定向；工作区切换器统一进入新的连续画布或当前已解锁阶段。
  - 左侧栏不再提供计划、结果、集合的并列跳转；阶段栏收敛为任务解析、文献筛选、证据研究三个用户阶段。
  - 重构候选页，加入真实运行摘要驱动的处理台账、候选检查器、全文与题录准入记录，以及确认构建集合的二次确认弹窗。
- **Error:**
  - 第一次 Playwright 模拟 API 验收在连续画布的终态标题断言失败；下一次将输出实际页面标题和可见文本，区分路由守卫、模拟响应和组件状态问题后再调整。
  - 终态页面诊断确认标题与路由均正确；第二次完整验收在移动端的后续可见性断言失败，待单独检查弹窗与检查器的响应式布局。
  - 新增 Playwright 工作流用例时发现候选 API 模拟路径缺少模板字符串结束符；已在执行前修复，准备重新运行。
  - Playwright 工作流用例已加载候选页，但两个断言均找不到带名称的 `dialog`；待检查失败上下文，改用弹窗稳定属性定位并确认 ARIA 名称兼容性。
  - 失败上下文确认弹窗实际存在，其可访问名称来自构建标题而非“确认研究集合”；为其增加稳定 `data-testid` 后，工作流用例通过。完整检查首次仅因首页模板未格式化而提前结束，待格式化后重跑。
- **Verification:**
  - `vue-tsc --noEmit`、ESLint、Prettier 检查与 Vite production build 全部通过。
  - Vitest：`4 passed`。
  - Playwright：认证 2 条、连续画布与集合确认 2 条，共 `4 passed`；后两条覆盖桌面与 390px 窄屏。
  - 浏览器用例使用 API 契约模拟，未触发真实模型、Provider 或入库 Worker；真实服务环境的主路径验收仍属于 Phase 7 既有待办。

### Phase 7.2: 真实联调状态修复

- **Status:** complete
- **Started:** 2026-08-01
- 已完成诊断：
  - 使用真实 FastAPI、PostgreSQL、Redis 与 Vite 服务创建/恢复工作区并跑至候选筛选阶段。
  - 确认后端工作区阶段、当前检索运行和 49 条候选统计均正确；问题集中在前端恢复与状态分支。
  - 确认题录未就绪时的永久“正在渲染”来自禁用 Query 仍处于 pending 的 UI 判断。
  - 确认全文被服务端返回 `rejected` 后仍持续轮询，原因是前端 `FulltextStatus` 和终态列表遗漏该状态。
- 已完成实施：
  - `ResearchRunnerView` 在 `screening` 等已越过计划确认的阶段读取当前运行，并根据运行终态跳转至结果页或集合页，不再要求重复确认计划。
  - `SearchRunView` 优先显示后端持久化的 `candidate_count`；SSE 统计改为合并更新，终态重新读取当前运行快照。
  - 题录预览只在 `citation.status = ready` 时显示加载、格式选择和复制操作；冲突或未完成状态展示明确原因。
  - `rejected` 已加入前端全文终态；未通过题录核验的候选不显示全文按钮，轮询在拒绝、失败或可用时停止。
  - 移除结果页和详情页对“尚未请求全文”候选的后台状态探测，避免服务端正确返回 404 时制造浏览器控制台错误。
- 验证：
  - `pnpm run typecheck`、`pnpm run lint`、`pnpm run test:unit` 通过，Vitest 为 3 个文件、11 个断言。
  - 真实服务首次浏览器回归：`/workspace/0066e4cf-a9cf-4087-a7f1-1d3beae013c2/run` 自动跳转结果页，显示 `49 / 49` 条候选；真实 `conflict` 候选详情没有全文按钮，也不再显示题录加载假象。
  - 初次回归发现两条 404 控制台记录，均来自随后已移除的无全文状态探测。
  - 最终真实浏览器复跑通过：工作区恢复到 `results?run=f454d6ec-3f65-40f7-bc42-b4a70f2cf7e4`，读取 49 条真实候选，冲突题录没有全文入口，控制台错误数为 0。

## Session: 2026-08-02 RAG 研究对话真实验收收尾

- **Status:** Phase 8.1–8.4 与 Phase 9 complete；Phase 8.5 的真实故障基线仍 in progress。
- 重启旧 FastAPI 进程后确认 OpenAPI 已暴露 `/api/v1/collections/{collection_id}/conversations`，解决研究对话页 404。
- 使用临时账号和真实工作区完成浏览器联调：登录、研究对话页加载、新建会话、会话侧栏折叠、移动端会话抽屉打开/关闭、删除确认均通过。
- 修复窄屏直接隐藏研究会话侧栏的问题：移动端改为可关闭的抽屉，并补充移动端图标按钮的 `aria-label`、`title`、`aria-expanded` 和 `aria-controls`。
- 真实单轮 RAG 验收已通过：Qwen embedding 1024 维、Milvus 命中 2 个 L3 片段、DeepSeek 生成带 2 条证据引用的回答、Redis 产生 5 个公开进度事件；临时数据已清理。
- 前端检查通过：Node 20 下 `vue-tsc`、ESLint、Prettier、Vitest（11 assertions）和 Vite build。
- 后端检查通过：`127 passed, 9 skipped`、Ruff、格式检查、Pyright、`alembic check`；新增 Alembic 过滤器排除 LangGraph 自有 checkpoint 表，避免误生成删除第三方表的迁移。
- 保存真实浏览器验收截图：`output/playwright/research-chat-desktop.png`、`output/playwright/research-chat-mobile.png`。
- 未完成：真实多论文复杂比较、Worker 重启/取消及外部依赖瞬时失败的故障基线，不能用离线图测试替代。

## Session: 2026-08-02 候选相关性评估 Agent 实施规划

- **Status:** planned
- 用户指出前端“候选理由”过于技术化，并确认需要真正的 Agent 判断，而不是当前的关键词匹配规则。
- 已将后续工作作为 Phase 10 写入 `task_plan.md`：先定义可验证的评估契约，再将结构化 DeepSeek/LangChain 评估插入搜索 Worker，随后暴露 API/SSE 状态、替换前端规则并完成真实验收。
- 核心不变量：相关性理由必须可回到标题或摘要证据；模型失败必须显式失败；评估只辅助用户筛选，不能突破既有 DOI、题录和全文准入规则。
- 规划文件首次批量补丁因 `findings.md` 没有预期标题而未应用；已读取实际文件尾部后改用精确上下文完成写入，未改动业务代码。

## Session: 2026-08-02 候选相关性评估 Agent 实施

- **Status:** in_progress
- 已重新审视讨论稿与当前 `SearchExecution`：现有统一候选在后端形成，但推荐理由仍由前端关键词规则生成；题录补全目前先于运行完成。
- 实施决策：候选快照先展示，统一候选批量送入 DeepSeek 评估，结果逐批写回 Redis/SSE；题录核验迁出语义评估关键路径，仅对高相关候选预取或由用户动作按需触发。
- 前端采用现有工作台视觉，不改变信息架构；只替换理由数据来源，并提供评估中、信息不足和失败的清晰状态。
- 后端 `ruff`、`pyright` 定向检查已完成。首次将前端 typecheck 追加在 backend 工作目录的同一命令中，90 秒后超时；该命令不作为验证结论，后续将拆分执行。
- 已新增统一候选的相关性字段、DeepSeek/LangChain 批量评估器和 `relevance_assessment` Worker 阶段；评估结果逐批写回同一 Redis 搜索快照，模型引用的标题/摘要片段会由服务端二次校验。
- 已将结果页从 `candidate-reason.ts` 的关键词匹配迁移为服务端评估结果展示；前端 Node 20 `pnpm typecheck` 通过。

## Session: 2026-08-02 候选相关性评估 Agent 续作

- **Status:** in_progress
- 恢复现有 Phase 10 计划并完成实现审计：统一候选、Redis 快照、SSE 分批写回和 DeepSeek/LangChain 批量模型调用已经存在。
- 续作范围已确认为：补足面向用户的研究内容概述字段；让模型配置与单项结果失败保持为候选级可见错误；增加授权、幂等的单项重试；删除前端关键词理由残留；补齐单元、API、浏览器和真实模型验收；同步讨论稿并审计提交。
- 已扩展后端评估契约：`study_focus` 专门回答“它主要研究什么”，不再让前端把整段摘要伪装为 Agent 总结；无摘要候选保留明确的信息不足结论。
- 已将同一模型批次的验证改为逐候选处理：有效且证据可回溯的结果会保留，缺失、重复或证据不在原文中的单项才显示失败；模型整体不可用时也不会覆盖无摘要候选的“信息不足”状态。
- 已补上基础初筛与语义评估的状态边界：未通过基础初筛的记录标为 `skipped`，不再永久显示“正在分析”，也不消耗模型调用。
- 已新增候选单项重试服务和 Redis 租约锁设计：只允许终态搜索会话、当前用户拥有且明确可重试的失败候选重跑；重试前先写入 `pending`，重复点击直接读取同一快照，不会叠加模型请求。
- 前端已改为服务端评估快照的单一呈现函数，并删除旧 `candidate-reason.ts` 关键词规则及其测试；列表将显示一句“为什么保留”，检查器显示 Agent 概述、帮助、局限、证据和重试操作。
- 定向后端测试已通过 8 项；首次 Ruff、格式和 Pyright 检查失败，原因已记录为导入排序、行宽、Redis `eval` 类型标注和 FakeModel 协议签名，不会忽略或重复原命令后直接提交。

## 2026-08-02 — Phase 10 migration verification

- Ran `uv run alembic upgrade head` from `backend/`.
- The migration did not apply. PostgreSQL reported that the migration tried to drop `ck_search_runs_ck_search_runs_ck_search_runs_stage`, while the migration source currently names `ck_search_runs_ck_search_runs_stage`; this indicates Alembic naming conventions are being applied again to an already-expanded constraint name.
- Next action: inspect the actual local Compose service and the live `search_runs` constraint name, then make the migration use an explicit, convention-safe identifier.

- Confirmed the live historical name as `ck_search_runs_ck_search_runs_stage`. Replaced convention-aware drop operations with exact `ALTER TABLE ... DROP CONSTRAINT` statements for the historical and upgraded names.
- Re-ran `uv run alembic upgrade head`, `uv run alembic current`, and `uv run alembic check`: all passed; local database is now at `d4f8c2a9b715 (head)`.

## 2026-08-02 — Phase 10 real-model acceptance

- Added `tests/integration/test_live_candidate_relevance.py`, gated by `RUN_LIVE_CANDIDATE_RELEVANCE_TESTS=1`; it only sends one controlled, normalized candidate to the configured chat model and validates that all returned evidence can be found in the supplied title or abstract.
- Targeted tests, Ruff check/format, and Pyright passed (`8 passed, 1 skipped`; `0 errors`).
- First live DeepSeek run reached the configured `deepseek-v4-flash` endpoint but produced a candidate-level `failed` state. The evaluator intentionally hid the remote implementation detail from the user-facing candidate snapshot; next action is a direct diagnostic call to determine whether the issue is model JSON-mode support or a returned payload validation failure.

## 2026-08-02 — Phase 10 completion

- Direct diagnostic showed that DeepSeek JSON mode returned a flat candidate object rather than the previously requested nested `assessment` wrapper, and may use a scalar for a single limitation/evidence item. The model content was otherwise usable and evidence-grounded.
- Replaced the model-only transport schema with an explicitly documented flat shape. The service normalizes only unambiguous single-item JSON forms, resolves missing evidence source fields from the candidate's own title/abstract, then converts to the unchanged `CandidateRelevanceAssessment` API contract and validates every quote. No keyword fallback was introduced.
- Added `WORKFLOW_RELEVANCE_ABSTRACT_MAX_CHARACTERS=3000` and `WORKFLOW_RELEVANCE_MAX_OUTPUT_TOKENS=2400`; batches remain sequential, so the evaluation concurrency is explicitly one. Updated both environment templates, backend guide, and the search/results/frontend/development discussion documents.
- Added the priority/background/manual-review filters to the existing candidate filter row. They only read server-side relevance states and keep low-priority records visible and user-selectable.
- Corrected the candidate evidence rendering key to use the user-facing label and quote, not an internal field removed by the presentation mapper. Updated Playwright fixtures to mirror the server candidate contract.
- Real DeepSeek acceptance passed repeatedly. A controlled live search with each provider capped at two records completed `partial_failed`: OpenAlex and Crossref returned four total included candidates, all four completed relevance assessment; Semantic Scholar timed out and was correctly isolated. The integration test cleaned its temporary PostgreSQL and Redis records.
- Final verification: backend `136 passed, 10 skipped`, Ruff, format, Pyright, Alembic check; frontend typecheck, ESLint, Prettier, `16` unit tests, `4` Playwright flows, and production build all passed using Node `20.19.6` and pnpm `10.34.5`.
- Pre-commit audit corrected `.env.example`: `SEARCH_CITATION_ENRICHMENT_LIMIT` now documents and restores the intended default `12`, so template behavior matches the worker's small high-relevance prefetch policy. Local `.env` was not changed.

## 2026-08-02 — Phase 11 发布前审计与提交

- **Status:** in_progress
- 已按 `planning-with-files` 恢复既有计划、发现和进度，并核对本轮实现边界：统一候选在后端生成，相关性判断由服务端模型评估结果驱动，前端只展示同一 Redis/API 快照。
- 已确认讨论稿、环境模板和后端说明覆盖候选渐进评估、证据回链、失败重试、题录按需补全和模型预算配置。
- `git diff --cached --check` 已通过；发现 `d4f8c2a9b715_add_search_run_relevance_stage.py` 在 pre-commit 自动格式化后仍有未暂存的最终工作区版本，后续验证完成后需要重新 `git add -A`。
- 后端复验通过：`136 passed, 10 skipped`；`ruff check`、`ruff format --check`、`pyright` 与 `alembic check` 均无错误。
- 前端复验通过：使用 Node `20.19.6` 与 pnpm `10.34.5` 执行 typecheck、lint、Prettier、Vitest（`16 passed`）、Playwright（`4 passed`）与生产构建均成功。
- 完成暂存区审计：152 个文件的差异通过 `git diff --cached --check`；十份讨论稿均已纳入；`.env` 未暂存；对新增差异执行密钥模式扫描未命中。
- 已创建本地提交 `feat: 完成研究工作流与候选相关性评估`；pre-commit 的合并冲突、YAML、文件结尾、尾随空格、Ruff、Ruff format 和 Prettier 检查均通过。

## 2026-08-03 — Phase 12 候选审核与集合准备交互规划

- **Status:** planned
- 用户指出结果页缺少真正的候选选择和分页，导致“确认 0 篇入集合”没有可理解的前置操作。
- 已审计当前实现：右侧检查器焦点与待确认集合数量被错误地放在同一视觉语境中；单篇全文准入存在，但没有跨页准备清单或批量操作。
- 已将 Phase 12 写入 `task_plan.md`，按交互边界、Redis/API 与批量核验、Vue 审核体验、真实端到端验收及讨论稿/提交四个阶段执行；本轮仅完成规划，尚未修改业务代码。
- 复核后已修正计划章节顺序：Phase 10.3–10.5 保持在 Phase 10 内，随后依次为已完成的 Phase 11 与待实施的 Phase 12；未改动业务代码。

## 2026-08-03 — Phase 12 候选审核与集合准备交互实施

- **Status:** in_progress
- 已开始实施。后端将复用现有单篇题录、全文与集合准入服务；准备清单和批量操作进度只写入 Redis 搜索会话，长期集合仍由 PostgreSQL 承载。
- 首轮静态检查暴露两个实现错误：候选审核错误映射插入时打断了既有相关性重试的返回语句，且 Redis 候选快照的处理统计缺少显式字典校验。已按检查位置修复；格式和类型检查将重新执行，不能将其视为无关告警。
- 候选审核单元测试首次将 `available` 全文状态构造为没有已验证文件的非法结果，Pydantic 正确拒绝。测试已改为合法的 `queued` 状态，继续验证分页、跨页选择与状态汇总边界。
- 续作恢复时，PowerShell 用于分段阅读的插值字符串将 `$file:` 误解析为变量名，命令在未读取源码前失败；后续改用 `${file}` 形式，不重复同一错误写法。该错误没有修改业务文件。
- 误从仓库根目录读取 `package.json`，该文件实际位于 `frontend/package.json`；PowerShell 报路径不存在。后续前端验证固定在 `frontend/` 工作目录执行，不把这一诊断命令视为验证失败。
- 后端 Phase 12 定向验证通过：`8 passed`，Ruff、Ruff format 与 Pyright 全部无问题。
- 直接执行系统默认 `pnpm` 时，Codex Desktop 注入了 Node `24.14.0` 和 pnpm `11.9.0`，与项目锁定的 Node `20.19.6`、pnpm `10.34.5` 不兼容而在依赖状态检查前失败。后续将显式使用项目 Node 20 与 pnpm 10，不修改锁文件或 engines。
- 使用工作区依赖探测工具时首次将返回值误按 MCP `content` 数组解析，实际返回为字符串；随后按字符串读取成功。另一次递归搜索 Node 安装目录在 60 秒超时，仅确认 `E:\nodejs\node.exe` 已在 PATH；后续改为直接检查该已定位路径和版本管理器目录。
- 用 `E:\nodejs\corepack.cmd` 已确认运行时为 Node `20.19.6`、pnpm `10.34.5`；前端 typecheck 随后暴露候选列表改为分页响应后，`PaperDetailView` 仍使用旧的全量 `candidates` 响应。这会使详情页不仅类型失败，也无法读取非第一页候选。修复方向是增加受所有权保护的单篇候选审核读取接口，详情页改为直接读取当前候选，而不是扫描分页列表。
- 已新增单篇候选审核读取接口并改造详情页读取方式。首轮 Ruff 只发现路由 contracts import 排序，已由 Ruff 自动修复；随后后端定向 `8 passed`、Ruff、格式和 Pyright 均通过，前端 Node 20/pnpm 10 typecheck 也通过。
- 前端 ESLint 已通过；Prettier 检查发现 `PaperDetailView.vue` 与本轮重写的 `ResultsView.vue` 未符合项目格式，导致后续 unit/build 未执行。下一步仅对这两份源码运行项目 Prettier，再重新执行完整前端验证。
- 已对两份 Vue 源码完成 Prettier，ESLint 与格式检查随后通过。Vitest 仍有一条旧断言把“题录冲突”视为不能开始全文任务，但真实 Worker 会先重新补齐题录；测试应更新为“带 DOI 且尚无全文任务即可开始核验”，再复跑 unit/build。
- 已更新全文入口的单元测试语义，typecheck 与 ESLint 通过；Prettier 仅提示该测试文件本身尚未格式化，因此 unit/build 尚未再次执行。接下来格式化此测试后复跑。
- 前端复验已通过：Node `20.19.6` + pnpm `10.34.5` 下 typecheck、ESLint、Prettier、Vitest（`16 passed`）和 Vite production build 均成功。
- 新增的 Playwright 审核流程首次运行发现两处模拟契约错误，而非页面业务逻辑：新测试把候选选择端点错误拼成了 `/candidates/candidate-selection`；旧工作流模拟没有按新的服务端 `filter` 参数筛选分页响应。将分别修正模拟路径与筛选返回值后复跑完整 E2E。
- 第二次 E2E 已使旧认证和工作流用例通过；新审核用例先遇到表格单元格的严格定位歧义，已改为锁定候选行的标题单元格。第三次只剩断言同时匹配表格、检查器和证据摘要中的同一标题；下一步将断言范围限定为候选表格，避免把正常的多处标题呈现误判为失败。
- 第四次 Playwright 全部通过：认证、既有工作流桌面/窄屏与新增候选审核流程共 `5 passed`。新增流程覆盖跨页选择、行点击不改变多选、刷新恢复、已选筛选、批量核验、批量准入和待确认集合计数同步。
- 浏览器真实页面可访问但处于登录页。未读取用户浏览器存储或猜测现有账户密码；后续真实验收将使用临时、可精确清理的本地 API/Redis 数据，而不操作用户已有工作区。
- 一次用于查找测试账户说明的 `rg` 同时传入不存在的仓库根 `tests` 路径而返回错误；有效搜索结果未发现可公开复用的测试账户。后续搜索限定实际 `backend/tests` 路径。
- 基于真实 PostgreSQL、Redis 与 FastAPI ASGI 的临时数据验收已跑到最后清理分支：候选分页、Redis 选择、单篇详情、批量核验任务投递、非法游标和跨账号隔离均通过。最后断言仍使用旧的 `search_run_session_expired` 错误码，而新审核服务正确返回 `candidate_review_session_expired`；将同步断言后重跑。测试 `finally` 已执行临时资源清理。
- 真实验收复跑通过：临时账号、PostgreSQL、Redis、FastAPI、MinIO 和真实批量准入路由完成“Redis 准备清单 -> 批量核验任务 -> 可处理全文状态 -> 批量加入待确认集合”。测试在 MinIO 中上传并转正最小 PDF，确认 PostgreSQL 待确认入库记录与 API 集合统计同步，随后精确删除正式/暂存对象、Redis 键、用户、工作区和测试唯一 DOI 论文。外部 Provider、模型和下载网络未在本轮重复调用。

- 真实 arXiv 专项验收首轮在外层 64 秒命令时限前被终止。随后用 `curl` 验证直连可达但 20 秒仅收取约 0.56 MB；`127.0.0.1:7897` 代理可达且同期收取约 1.86 MB。因此第二次验收仅以进程环境显式启用全文代理，不修改仓库或 `.env`。
- 代理下真实 Worker 已成功下载 arXiv PDF、校验、写入 MinIO、批量准入；专项测试随后因错误假设 `Document.latest_ingestion_run` 关系而失败。实际模型使用 `Document.ingestion_runs`，已将测试改为按文档 ID 查询 `IngestionRun` 并验证其初始 `pending` 状态，待复跑。
- 真实 arXiv 专项验收已在代理模式下通过（`1 passed in 26.69s`）。发布前定向单元测试随后通过 `11 passed`；Ruff 发现专项测试的未使用导入和超长行，已按报告位置修复，待重新执行静态、前端和全量回归。
- Phase 12 收尾验证完成：后端候选审核定向测试 `11 passed`、Ruff、Ruff format 和 Pyright 通过；后端全量 `144 passed, 11 skipped`，Alembic check 无待生成迁移。前端在 Node `20.19.6` 与 pnpm `10.34.5` 下通过 Prettier、ESLint、vue-tsc、Vitest（`16 passed`）、Playwright（`5 passed`）与生产构建。
- 已同步检索、结果、前端体验、前端交互和开发环境讨论稿，并新增 `frontend/README.md`。文档明确“正在查看 / 本次准备清单 / 待确认集合 / 可研究集合”四层边界、游标分页、批量核验与严格准入顺序。
- Git 提交前审计通过：`git diff --check` 无输出，`.env` 未被跟踪，差异密钥前缀扫描未命中；已创建本地提交 `feat: 完善候选审核与批量准入`。

## 2026-08-03 — Phase 13 检索等待页可验证进度体验

- **Status:** complete
- 用户指出检索等待页缺少可感知进度，容易让人误以为任务卡住。已审计现有 SSE 和 Worker：相关性评估的总数、完成数和失败数已经真实发布；问题是前端未使用这些统计，且处理轨迹漏掉该阶段。将以真实阶段和计数重组页面，不显示虚假百分比、剩余时间或模型内部思考。
- 已在检索画布加入“相关性判断”阶段；运行中使用真实 `included_candidate_count` 和相关性总数/完成数/失败数呈现“已找到 N 篇候选，正在逐篇判断相关性”与“已分析 N / M 篇”。
- 已展示真实 SSE 消息与最近更新提示。15 秒未收到新事件或连接异常时保留现有进度并允许手动重新连接；主动终止旧连接不会误报“进度读取失败”。来源失败会明确说明其他来源的候选仍继续处理。
- 新增单元测试覆盖候选数兼容与相关性计数转换；新增 Playwright 流程覆盖运行中的相关性计数、SSE 消息和来源失败说明，并更新完成态候选数断言。
- 以 Node `20.19.6`、pnpm `10.34.5` 完成 Prettier、ESLint、vue-tsc、Vitest（18 passed）、Playwright（6 passed）和 Vite production build 验证。浏览器本地 smoke 访问确认应用认证入口正常，未读取或操作任何用户会话数据。

## 2026-08-03 — Phase 14 文献核验任务页与待确认集合衔接

- **Status:** in_progress
- 用户确认当前“准备核验”仅在候选行显示“全文处理中”，缺少批量任务上下文和与待确认集合的协作入口。已建立实施计划：先复核现有 API 契约，再以独立核验任务页承接真实状态和可入集合动作；不在候选列表中堆叠长任务详情。
- 审计完成：现有候选审核 API 已支持 `selected` 服务端分页、全文状态与准备清单汇总；批量准入会移除成功项而保留未完成项。本阶段无需新增后端持久模型或复制严格准入规则。
- 已恢复 Phase 14 计划，并按项目规则启用 `design-taste-frontend` 进行页面审计。设计判断：面向需要理解异步文献准入过程的研究者，保持浅色学术工作台的可验证、克制风格；采用 `DESIGN_VARIANCE=3`、`MOTION_INTENSITY=3`、`VISUAL_DENSITY=5`，不新增营销式视觉元素或虚假进度。
- 计划文件首次更新补丁因 `findings.md` 标题层级不匹配未能应用；已读取文件尾部后改用实际段落锚点继续更新，不重复同一补丁写法。
- 已完成第一轮实现：核验页增加真实交接轨迹、自动刷新提示、任务加载骨架、重新读取入口、可读的受阻状态和窄屏布局；候选结果与论文详情页均不再绕过核验页直接加入待确认集合。详情页的单篇核验会同步把候选加入本次准备清单，确保后续可在同一任务页交接。
- 已将浏览器流程测试改为“跨页选择 -> 进入核验任务 -> 部分核验完成 -> 确认加入可用项”的真实 API 模拟路径，并为 Worker 公开状态映射补充单元测试；讨论稿同步更新待完成格式与验证后复核。
- 首轮 Playwright 回归中，核验页新流程本身已完成跳转，但测试的“题录与全文核验”短语同时命中流程轨迹、说明和两条文献明细，触发严格定位错误。已将断言收紧为精确流程节点文本，再复跑，不修改业务实现。
- 第二轮同样发现“已通过核验”出现在任务说明与状态标签中，属于测试定位问题。已改用精确状态标签断言；后续测试将优先使用精确可访问名称或限定容器，避免把页面为了可理解而提供的重复语义误判为缺陷。
- Phase 14 已完成。前端 `format:check`、ESLint、vue-tsc、Vitest（19 passed）、Playwright（6 passed）和生产构建均通过。差异空白检查与新增差异敏感信息扫描均无问题；未执行 Git 提交，保留当前工作区供用户审阅。

## 2026-08-03 — Phase 15 实施对齐整改 P0

- **Status:** complete
- 已按 `planning-with-files` 恢复计划、发现和进度，并核对工作区中未提交的 Phase 13/14 前端与讨论稿改动；这些改动将保留，不作为本轮整改的回滚对象。
- 已把 `docs/11-implementation-alignment-discussion.md` 的 P0/P1/P2 顺序写入持久计划。当前先实现和验证 P0：完整候选集合的一次结构化相关性判断、相关性优先稳定排序/游标、以及与真实能力一致的文档状态。
- 首次更新三个规划文件的组合补丁因 `findings.md` 标题实际为三级标题而未能匹配，未写入任何文件；已改用各文件的精确锚点继续，不重复失败补丁。
- 已完成 P0 源码第一轮改造：移除相关性串行批次配置，改为显式的完整集合容量上限；Worker 在上限内只调用一次 Agent，在上限外发布可展示、不可单项重试的缩小检索提示。缺摘要候选仍在完整集合输入中，并由服务端确定性标为信息不足。
- 已让候选审核在终态使用 `core -> related -> background -> not_recommended -> insufficient_information -> pending -> failed -> skipped` 的相关性优先顺序；同层再按来源数、开放获取、年份、标题和候选 ID 稳定排序。运行中继续使用发现排序，游标包含排序版本。
- 源码首个组合补丁因候选相关性模块的调用参数行与预期锚点不一致而未写入；随后已按精确代码片段拆分应用，不重复失败补丁。
- P0 定向测试首次失败不是业务行为：测试直接构造 `WorkflowSettings` 时未提供必需的测试聊天凭据，Pydantic 按预期拒绝。已在测试构造中补入 `SecretStr("test")`，随后复跑相同定向测试。
- 后端静态检查首次发现测试文件的函数名行宽、导入排序，以及测试替身注入到窄类型构造参数的 Pyright 报告。已缩短测试名、按项目规则重排导入，并仅在测试边界使用精确 `cast`；随后重新运行静态检查。
- 复核异常分支时发现旧逻辑会在模型不可用时把缺摘要候选也误标为失败，这与“缺摘要确定性信息不足”的边界冲突。已使全缺摘要集合不依赖模型配置，并在模型不可用的混合集合中保留缺摘要候选的确定状态；新增回归测试。
- P0 全部完成：后端全量 `150 passed, 11 skipped`，Ruff、格式、Pyright 与 Alembic check 通过；前端 Node `20.19.6` / pnpm `10.34.5` 的 typecheck、ESLint、Prettier、Vitest（19 passed）、Playwright（6 passed）和生产构建均通过。已同步 README、环境模板、开发说明、产品/检索/RAG/治理讨论稿与实施对齐稿；Phase 15 标记完成，P1/P2 保持 planned，未冒充已实现。
- 最终差异审计确认 `git diff --check` 无空白错误，旧的相关性批处理配置和“RAG 对话尚未实现”文案均无残留。首次用 Bash 风格 `|| exit 0` 处理 PowerShell 的空搜索结果失败，已改用 `$LASTEXITCODE` 检查完成复验。

## 2026-08-03 — Phase 15.1 P0 真实模型与基础设施验收

- **Status:** complete
- 用户要求进行真实测试。本阶段将把受环境变量保护的真实模型验收扩展为多候选完整集合，再检查本地基础设施并在不读取或修改既有用户数据的前提下执行可用的检索/审核专项验收。
- 已将真实模型测试从单候选改为同一集合的两条统一候选；测试只输出通过摘要，逐条验证服务端返回的标题/摘要证据，不输出模型原始响应。
- 真实模型验收通过：`RUN_LIVE_CANDIDATE_RELEVANCE_TESTS=1 uv run pytest tests/integration/test_live_candidate_relevance.py -m live -q -s` 返回 `1 passed in 9.09s`，确认同一次调用完成 2 条候选的受证据约束判断。
- 基础设施健康检查确认 Redis 与 PostgreSQL 可读写服务可用，Compose 中 Milvus、MinIO、PostgreSQL、Redis 均 healthy。`RUN_LIVE_CANDIDATE_REVIEW_E2E_TESTS=1 FULLTEXT_NETWORK_MODE=proxy uv run pytest tests/integration/test_live_candidate_review_e2e.py -m live -q -s` 返回 `1 passed in 18.56s`；测试以临时用户完成 Redis 准备清单、真实 arXiv PDF 下载/校验、MinIO 暂存/转正和 PostgreSQL 准入，并在 `finally` 精确清理。

## 2026-08-03 — Phase 15.2 P0 当前环境真实复验

- **Status:** in_progress
- 用户要求先真实测试。本轮不复用历史通过结论，将重新执行受环境变量保护的真实模型与真实候选准入专项。
- 首次服务检查从仓库根目录运行 `docker compose ps` 返回“no configuration file provided: not found”；根目录没有 Compose 配置。随后的 Docker 状态检查确认 `academic-search-dev-postgres-1`、`academic-search-dev-redis-1`、`academic-search-dev-minio-1` 与 `academic-search-dev-milvus-1` 均为 healthy。PostgreSQL 映射端口是 `55432`，因此对默认 `5432` 的连通性探测失败不表示服务异常；Redis `6379`、MinIO `9000`、Milvus `19530` 均可连接。
- 真实模型专项已重新执行：`RUN_LIVE_CANDIDATE_RELEVANCE_TESTS=1 uv run pytest tests/integration/test_live_candidate_relevance.py -m live -q -s` 返回 `1 passed in 7.65s`。同一次模型调用处理 2 条候选，逐条标题/摘要证据回链断言均成立。
- 真实候选准入专项已重新执行：`RUN_LIVE_CANDIDATE_REVIEW_E2E_TESTS=1 FULLTEXT_NETWORK_MODE=proxy uv run pytest tests/integration/test_live_candidate_review_e2e.py -m live -q -s` 返回 `1 passed in 18.08s`。链路实际完成 arXiv PDF 下载/校验、Redis 准备清单、MinIO 暂存/转正与 PostgreSQL 准入。
- 测试的 `finally` 清理随后经过独立只读数据库核查：临时用户邮箱模式 `live-candidate-review-%@example.invalid` 剩余 `0` 条。Phase 15.2 完成；本轮未修改业务实现，P1/P2 仍未实施。

## 2026-08-03 — Phase 16 实施对齐整改 P1

- **Status:** in_progress
- P0 当前环境真实复验已通过，现按用户此前的完整实施请求进入 P1。范围严格限制为候选理由证据核验、受权 PDF 上传、可替换真实重排、单轮回答独立证据核验及相应契约/前端/讨论稿验收。
- 已重读实施对齐讨论稿并完成 P1 范围审计：四项均是当前能力缺口，后续先定位现有候选相关性、全文暂存、RAG 检索与回答生成服务，再写离线契约；不在本阶段改变 P2 的复杂路由、取消、配额或指标。
- 已完成候选相关性与 RAG 主路径初查：候选理由只有引文字面匹配；RAG 当前是 RRF/父块合并后的直接截断；现有回答前片段筛选不是回答后主张核验。下一步将审计全文状态机和 API，再确定复用点。
- 已完成全文状态机审计：服务端已有候选所有权校验、Redis 短期全文状态、PDF 暂存校验和严格准入。上传将复用该状态与准入服务，不新增能绕过 DOI/题录/PDF 门槛的旁路；下一步定位现有单篇 API、前端调用和 RAG 图的精确节点。
- 已确认现有候选审核 API/页面没有上传端点或授权提交，`requires_upload` 只是提示。后续会以原有候选详情和核验任务为入口，在同一 Redis 全文状态中恢复可入集合的结果。
- 候选理由独立核验已开始实现。首轮 `test_candidate_relevance.py` 出现 5 项失败：新注入参数误写入核验器构造函数，而不是评估器构造函数。已按实际类定义移动该参数并复跑，`6 passed in 1.32s`；新增用例证明“引文存在但理由扩大解释”会被标为 `candidate_relevance_claim_unsupported`，不会保留 `relevance_assessment`。
### 2026-08-03 — P1 真实测试启动

- 恢复规划上下文后，确认当前阶段仍为 Phase 16（P1 进行中）；P0 的本轮真实模型和真实 PDF 准入复验已记录为通过，但不能替代 P1 的真实验收。
- 本轮先定位并运行 P1 新增链路的受环境变量保护的真实测试，结果以本次命令输出为准；在测试完成前不继续扩展实现。
- 工具编排层首次尝试将 shell 调用结果按对象的 `content` 字段读取，返回 `undefined`；已改为按字符串结果处理，未影响项目文件或测试环境。
- 已执行：`RUN_LIVE_CANDIDATE_RELEVANCE_TESTS=1 uv run --python 3.12 pytest tests/integration/test_live_candidate_relevance.py -m live -s`。结果：失败（`1 failed in 57.35s`）。两条候选至少有一条被 P1-A 独立主张核验标记为 `failed`，而测试目前只断言全部 `completed`，未输出具体失败码。
- 环境实际使用 Python `3.12.13`，符合后端 `>=3.12,<3.13` 约束；前一条无参数版本探测返回 Python 3.13.12，后续项目命令必须显式固定 `--python 3.12`。
- 已完成一次不写入项目数据的真实模型诊断调用。第一轮输出两条候选均通过逐字引文存在性校验，但分别加入了标题/摘要未说明的“保护作用”“机制”“发表偏倚”“残余混杂”“外推性”等表述；第二轮独立核验拒绝了全部候选。这证明拒绝逻辑在发挥作用，也证明第一轮生成约束不足以稳定产出可展示理由。
- 诊断脚本在输出失败对象时错误使用 `__dict__` 访问 slots dataclass，导致诊断进程以 `AttributeError` 结束；前序模型结果已经完整打印，未重复调用或修改测试数据。后续若需输出该对象，应显式读取 `code`、`message`、`retryable`。
- 已修改候选相关性第一轮系统提示：禁止将关联扩大为因果、保护作用、机制或结论；限制默认空数组且不能从研究设计推断；建议仅能是中性核对动作。真实专项改为断言安全不变量：通过二次核验的候选才能保留评估，拒绝候选必须清除评估且使用 `candidate_relevance_claim_*` 失败码。
- 静态检查第一轮显示候选核验模块仍有格式、行宽问题，单元测试同时通过 `6 passed`。已执行目标文件格式化和导入排序；剩余一条 113 字符的既有提示词行已手工换行，待重新运行静态检查。
- P1-A 候选理由复验通过：`RUN_LIVE_CANDIDATE_RELEVANCE_TESTS=1 uv run --python 3.12 pytest tests/integration/test_live_candidate_relevance.py -m live -s` 返回 `1 passed in 17.62s`。真实模型在同一集合调用中完成两条候选，二次核验后 `2 completed, 0 rejected`；专项不写入本地基础设施。
- P1-A RAG 真实 Worker 验收通过：`RUN_LIVE_RESEARCH_E2E_TESTS=1 uv run --python 3.12 pytest tests/integration/test_live_research_e2e.py -m live -s` 返回 `1 passed in 161.78s`。临时集合实际完成 embedding、Milvus 检索、DeepSeek 回答和回答主张二次核验；trace 为 `supported`、`claim_count=4`、`unsupported_claim_count=0`，Redis 记录 6 个事件，测试输出确认临时资源已删除。已把这三个 trace 条件加入真实用例断言，待复跑更新后的用例。
- 更新 RAG 真实用例后，Ruff 发现既有 `test_research_graph.py` 的导入顺序问题；已执行自动排序。`ruff format --check`、`ruff check` 均通过，离线图测试 `5 passed in 2.34s`。
- 带 answer-claim trace 断言的真实 RAG 回归通过：`RUN_LIVE_RESEARCH_E2E_TESTS=1 uv run --python 3.12 pytest tests/integration/test_live_research_e2e.py -m live -s` 返回 `1 passed in 150.48s`。trace 保持 `supported`、4 条主张、0 条不支持；Reranker 明确为 `disabled`，未产生伪 `rerank_score`；清理输出为 `deleted`。
- 已补 P1-B 真实基础设施专项：随机用户/集合/检索运行只在 PostgreSQL 与 Redis 中创建；PDF 以二进制流进入 `AuthorizedPdfUploader`，断言候选身份、授权来源、PDF 签名、大小与 SHA-256，再经既有 `CandidateReviewService.admit_selected()` 转正并生成待入库运行。测试不允许客户端传 URL、DOI 或对象键，且在 `finally` 清理 MinIO、Redis、数据库资源。
- P1-B 真实专项首次执行失败（`1 failed in 7.44s`）：授权上传本身已写入 MinIO 暂存并返回 `available`，但 `CandidateReviewService.admit_selected()` 在每项结算后回滚会话，导致最后读取已过期的 `run.id` 时触发 `sqlalchemy.exc.MissingGreenlet`。已保存 `run_id` 标量并在返回响应时使用，保留每项独立回滚及严格准入语义；待复跑。
- 第一次修复未命中 `admit_selected()` 的实际返回（误改到了相邻准备任务响应）；复跑仍以同一 `MissingGreenlet` 失败。现已在 `admit_selected()` 的运行读取后保存 `run_id` 标量，并由该方法的最终批量响应使用；这是针对同一根因的精确修复，待重新执行真实专项。
- 第二次上传复验已越过事务边界，但严格准入以 `FULLTEXT_MISMATCH` 阻止入库：上传器写入 `access_rights="user_authorized"`，而 `documents` 约束、准入白名单和讨论稿均只允许 `user_upload`、`open_access`、`official_allowed`。已统一上传结果和测试到现有 schema 值 `user_upload`；显式用户授权仍由 `x-upload-authorized=true` 与服务器端候选会话校验保证，待复跑。
- 统一权限枚举后的首轮静态检查发现 `acquisition.py` 保留两处既有 Ruff 格式差异；已执行目标文件格式化。相关 6 个文件的 Ruff format/check 通过，候选上传、PDF 校验和审核服务离线测试 `27 passed in 0.72s`。
- P1-B 真实授权上传专项通过：`RUN_LIVE_CANDIDATE_UPLOAD_E2E_TESTS=1 uv run --python 3.12 pytest tests/integration/test_live_candidate_upload_e2e.py -m live -s` 返回 `1 passed in 5.17s`。随机候选的 PDF 经 `AuthorizedPdfUploader` 以 `user_upload` 记录写入 MinIO 暂存，随后由既有准入服务转正、写入 PostgreSQL 并创建待入库运行；Redis 准备清单清空，`finally` 清理测试资源。
- P1-C 静态检查首次暴露 `retrieval.py`、`graph.py`、`execution.py` 的 3 处既有格式差异，以及 `test_research_retrieval.py` 的导入排序；已执行目标格式化和导入排序。Ruff format/check 通过，Reranker 单元测试 `4 passed in 1.87s`，覆盖禁用时无分数、启用时重排、HTTP 标准响应和返回下标边界。
- 当前 `.env` 未配置 `RAG_RERANKER_URL`、`RAG_RERANKER_API_KEY`、`RAG_RERANKER_MODEL`；真实 RAG Worker 验收已证明禁用分支明确写入 trace 并显示为 RRF 截断，但不能证明某个真实第三方重排服务可用。
- 已补 P1-B 浏览器回归：候选详情在 `requires_upload` 状态呈现授权上传面板；无文件或未确认授权时提交禁用，选择 `application/pdf` 并勾选声明后才发送现有上传接口的二进制请求和 `X-Upload-Authorized: true`，成功仅进入核验任务交接。
- 前端初次检查未执行：默认 `pnpm` 命令实际由 Codex fallback 的 Node 24 / pnpm 11.9.0 运行，违反项目 `Node >=20.19.0 <21`、`pnpm >=10.34.5 <11` engines。已确认本机项目 Node 为 `E:\nodejs\node.exe`（20.19.6）；需要通过该 Node 对应的 pnpm 10.34.5 启动后再重试，不能以 `--ignore-engines` 绕过。
- 已验证 `E:\nodejs\corepack.cmd pnpm` 使用 pnpm 10.34.5，并与 Node 20.19.6 配对；后续前端验证统一通过该命令执行。
- 前端格式检查在新增上传浏览器测试中发现一处未闭合的模板字符串，尚未执行 ESLint 或 typecheck；已修正该语法错误，接下来会用项目 Prettier 格式化该测试与关联视图后再运行检查。

### 2026-08-03 — P1 前端契约收尾

- **Status:** in_progress
- 恢复上下文后复核发现：后端真实状态 `requires_upload` 已被候选详情、核验任务和新增上传浏览器用例使用，但前端 `FulltextStatus` 联合类型尚未包含它。该遗漏同时阻断类型检查，并会让详情页继续轮询一个需要用户操作的终态。
- 已补齐 API 状态类型、终态判断、核验任务公共呈现和结果页状态说明；单测加入 `requires_upload` 的终态及受阻呈现断言。接下来使用锁定的 Node 20.19.6 / pnpm 10.34.5 依次执行格式、类型、单元和浏览器验收。
- 首次前端格式命令从 `frontend` 工作目录仍传入 `frontend/...` 路径，Prettier 正确报告找不到文件，类型检查没有启动。已记录为路径问题；下一次将使用 `src/...` 与 `tests/...` 的相对路径，不重复原命令。
- 前端格式化、`vue-tsc --noEmit`、ESLint 与 Vitest（5 files / 19 tests）已通过。全套 Playwright 首次执行则为 `3 passed, 4 failed`：失败页均出现 Vite 错误覆盖层并拦截点击；上传用例没有找到授权上传面板。下一步读取 Playwright 错误上下文和浏览器日志，按共同运行时根因修复后再执行受影响用例与全套回归。
- 错误上下文确认所有失败来自 `PaperDetailView.vue:239` 的 `v-else-if has no adjacent v-if`：上传按钮前的裸 `>` 破坏了条件分支链。移除该字符并格式化后，vue-tsc 再次通过；Playwright 复跑为 `7 passed in 5.2s`，包含真实浏览器下的授权 PDF 二进制上传、`X-Upload-Authorized: true` 请求头和核验任务交接。
- 最终前端审计通过：Prettier 全量检查、生产构建、`git diff --check` 无误。只读检查 `.env` 确认三个 Reranker 配置均缺失，P1-C 的真实 HTTP 服务调用仍无法执行；P1-A/P1-B 的实现、真实验收和前端回归已完成。Phase 17 已转为进行中，开始审计 P2 的独立实现范围。

## 2026-08-03 — Phase 17 实施对齐整改 P2

- **Status:** in_progress
- P2 审计确认三个实际缺口：复杂模式由关键词而非结构化判定触发；现有“复杂”链路只是一次规划加并行检索，不是可从工具观察中决定下一步的受限循环；研究运行只能在 Worker 领取前取消，且缺少用户配额、全局预算、阶段耗时与失败指标。
- 首次测试文件定位误以为存在 `test_research_service.py`，PowerShell 报路径不存在；已记录并改为用实际 `rg --files` 结果定位服务和执行层测试，不创建脱离当前测试结构的新文件。
- 后续将先设计最小持久化与 API 契约，再按“结构化路由 -> 受限循环 -> 运行中取消 -> 配额/预算/指标”的顺序实现；不跨越当前集合检索和已授权证据边界。
- 研究图测试的首个组合补丁因假定 `Sequence` 从 `typing` 导入而未匹配现有文件，未写入任何测试代码。已记录为精确锚点问题；后续将读取当前头部并拆分应用测试改动。
- P2 结构化路由、受限循环、调用预算和取消检查的首轮定向检查先报告两处 Ruff 格式差异，尚未运行静态规则或测试；下一步使用项目格式器处理这两个目标文件后继续。
- 格式化后 Ruff 继续发现 `asyncio` 已不再使用，以及受限循环 lambda 捕获循环局部预算变量。已改为先计算 `tool_calls_remaining` 并移除导入；接下来重新运行同一目标的静态检查和图测试。
- 第二次 Ruff 仍要求 lambda 在定义处绑定循环值；已改为默认参数绑定，并按三次错误协议采用与前一轮不同的精确修复后复验。
- P2 图测试首次运行有 6 项通过、1 项失败：复杂循环在所有计划查询都执行后直接收束，trace 未记录控制器“可以回答”的最终决策。已改为即使没有剩余查询也要求结构化控制器显式选择 `answer` 或 `clarify`，使每轮研究决策可审计。
- 后端 P2 定向 Ruff 已通过；Pyright 发现运行中取消的服务方法在理论末尾缺少响应。已将 queued/running 分支后的处理改为无条件抛出稳定冲突错误，随后复跑静态检查。
- 前端治理摘要的首次类型检查发现 `retrieval_trace` 是未知键记录，不能直接解引用预算字段。已添加运行时记录守卫；完成后将复跑类型、Lint 和浏览器流程。
# 2026-08-03 - P2 收尾恢复

- 根据 `session-catchup.py` 恢复 P2 上下文，确认图层、Worker、迁移、API 契约与研究界面已实现，定向研究图测试、后端定向 Ruff/Pyright 和前端 P2 静态检查均已通过。
- 发现终态阶段计时顺序缺陷：`complete()`、`fail()` 与 `_mark_cancelled()` 在关闭计时前设置终态 stage，会污染审计 trace；下一步先修复并补执行服务契约测试。
- 全量后端检查上一轮被两个既有的 Ruff import-order 问题阻塞，且本轮测试文件定位命令误用了 PowerShell 不支持的路径通配；均已记录，后续采用显式路径修复和执行。
- 已修复两个既有 Ruff import-order 问题；新增 `test_research_execution_service.py` 后，研究图与执行服务定向测试 `12 passed in 2.18s`。首次同命令中的 Ruff 路径带了重复 `backend/` 前缀而报 E902，测试成功掩盖了该退出码；已记录并改为拆分重跑。
- 后续正确路径的 Ruff 检查发现新增测试有一个未使用 `uuid4` 导入；已删除，准备以独立命令完成静态检查与定向回归。
- 全量 Ruff 已通过。Pyright 检出新增执行测试的可空 trace 类型，以及两个既有测试的 `Literal`/可空语言收窄问题；均已做无行为变化的精确修正，待重跑全量类型检查。
- 全量 Pyright 已通过（0 errors）。全量 pytest 首次为 `167 passed, 12 skipped, 1 failed`：候选相关性重试测试只替换首轮评估模型，遗漏 P1 的独立核验器；已注入通过型替身，准备重跑。
- P2 全量离线验收已通过：Ruff 格式/检查、Pyright（0 errors）及 pytest（`168 passed, 12 skipped`）。已执行本地 `alembic upgrade head`、`alembic check` 与 `alembic current`；治理状态迁移 `e5c7a9d1b208` 已应用并为 head，未检测到待生成操作。
- 已确认 P2 讨论稿要求真实验证结构化路由、受限 ReAct、运行中协作停止、额度/预算和阶段耗时；本地 Redis、Milvus 与 MinIO 监听端口存在。首次读取 compose/live 文件在 `backend` 目录内误带项目根前缀，已记录，待以正确工作目录检查完整基础设施与现有 live 验收脚本。
- 已新增 `test_live_research_governance_e2e.py`：用真实 PostgreSQL、Redis 和当前 Worker 函数，配合在路由调用中阻塞的测试模型制造运行中取消窗口；验收取消终态事件、无答案/证据写入、正确关闭 `preparing` 阶段计时，以及用户/全局每日限额。仅在显式环境变量下执行，并按随机资源精确清理。
- 新增真实治理专项的首次静态检查发现未使用 `ResearchRun` 导入；Pyright 已通过，默认不开启 live 环境变量时 pytest 按预期跳过。已删除导入，准备单独重跑并显式开启真实验收。
- 真实治理专项已显式执行并通过：`RUN_LIVE_RESEARCH_GOVERNANCE_TESTS=1 uv run --python 3.12 pytest tests/integration/test_live_research_governance_e2e.py -m live -s -q` 返回 `1 passed in 5.11s`。Ruff、Pyright 也已单独通过。
- 真实模型 RAG 专项两次失败均已清理临时资源：模型先输出 `router`、后输出 `choice` 而非 schema 的 `mode`，Worker 都按严格解析安全失败。已将输入规范化收束为“缺少 `mode` 且唯一已知别名”的规则，并扩展单元回归；下一次是该问题的最后一次真实模型复验。
- 最后一次真实模型 RAG 专项通过了路由阶段但超过 124 秒外层时限，未报告为通过。已按 `Live RAG evidence test collection` 与 DOI 测试前缀审计 PostgreSQL，未发现遗留集合、论文或运行，未执行第四次相同链路。P2 的真实 PostgreSQL + Redis + Worker 治理专项仍为通过状态。
- 已新增 `research-chat-governance.spec.ts`，以 Mock API 覆盖研究页显示结构化路由理由、模型/检索预算，以及运行中点击“请求停止”后呈现协作停止状态并隐藏重复操作入口。
- 新增浏览器用例首次运行确认页面已正确显示治理摘要，但文本与状态说明位于同一容器，精确文本定位失败；已改为对状态容器做包含断言，待重跑。
- 讨论稿同步前发现 P2 范围差：当前每日用户/全局预算只保护研究问答，尚未保护检索运行和集合构建入库。P2 保持进行中，下一步在这两个投递边界补同类限制、拒绝原因和测试，而不是提前更新讨论稿为完成。

## 2026-08-03 — P2 治理范围续办

- 已恢复 `task_plan.md`、`findings.md` 与 `progress.md`，确认当前应继续 Phase 17/P2，不能提前同步讨论稿为完成。
- 首次更新计划的补丁使用了已过时的 Phase 17 文本锚点，未写入任何文件；已按当前计划文本重新定位，不重复该锚点。
- 已定位搜索和集合构建的四个实际投递入口，以及研究问答现有配额实现作为复用语义参考。下一步实施搜索运行、入库运行的用户/全局 UTC 日限额、API 429 契约和相应离线/真实基础设施验收。

## 2026-08-03 — P2 检索与入库预算实施

- 已新增 `WorkflowSettings` 搜索用户/全局日限额、`IngestionSettings` 入库用户/全局日限额，补齐 `SearchRunErrorCode`、`CollectionBuildErrorCode` 及两个 API 路由的 HTTP 429 映射。
- 新增迁移 `a9f3c7d2e6b4_add_ingestion_run_submission_time.py`：`ingestion_runs.submitted_at` 只在实际进入 Worker 队列时写入，并建索引供日额度计数使用。已执行 `alembic heads`（唯一 head）、`alembic upgrade head` 和 `alembic check`，数据库为模型当前 schema。
- 定向 Ruff 首次仅发现 `document.py` 新字段的自动格式化差异；已使用项目格式器修复，随后 Ruff check 通过。定向离线测试为 `15 passed`，默认 live 开关为 `15 passed, 2 skipped`。
- 已显式运行真实 PostgreSQL + Redis + Worker 治理专项：`2 passed in 5.47s`。新增真实场景验证搜索的用户/全局拒绝、批量入库的用户/全局拒绝均发生在队列前，pending 运行没有状态或提交时间副作用。下一步执行全量后端与前端回归，再同步讨论稿并决定 P2 完成状态。

## 2026-08-03 — P2 完成收口

- 全量后端验收通过：`ruff format --check .`（170 files）、`ruff check .`、Pyright（0 errors）和 pytest（`176 passed, 14 skipped`）。首次 Pyright 只报告新 API 契约测试将 `HTTPException.detail` 直接下标访问；以显式类型收窄修正后通过，不涉及运行时代码。
- 数据库迁移已升级并复核：`a9f3c7d2e6b4 (head)`，`alembic check` 没有待生成操作。新迁移将 `submitted_at` 与索引加入 `ingestion_runs`，旧 pending 记录保持未提交，符合实际构建语义。
- 全量前端验收通过：Prettier、`vue-tsc --noEmit`、ESLint、Vitest（19 passed）、Playwright（8 passed）和 Vite production build；本轮类型变更只向前端 `IngestionRun` 增加可空 `submitted_at` 审计字段。
- 已同步 `05`、`06`、`11` 号讨论稿。P2 标为完成，P1-C 仍明确为“真实 HTTP Reranker 未配置”，真实模型长链 RAG 最后一次超时亦未记为通过。
- Phase 17/P2 已完成；不提交、不暂存或回滚当前本来就存在的脏工作区改动。
- 最终只读环境检查确认三项 Reranker 配置均 absent，P1-C 的真实服务验收继续保持未完成；`git diff --check` 通过，CRLF 仅为 Git 预告警。

## 2026-08-03 — P1-C Reranker 配置入口

- 按用户请求在实际 `.env` 增加 `RAG_RERANKER_URL`、`RAG_RERANKER_API_KEY`、`RAG_RERANKER_MODEL` 三项，均留空并附 `Qwen/Qwen3-Reranker-0.6B` 推荐说明。
- `ResearchSettings` 新增空值归一逻辑，防止待填写的空环境变量被误判为不完整配置而阻断启动。Ruff 格式/检查通过，`test_research_settings.py` 与 `test_research_retrieval.py` 为 `5 passed in 2.01s`；直接加载实际 `.env` 输出 `reranker_enabled=False`、`model_configured=False`。
- 首次直接配置加载探针把 `-c` 误传给 `uv run`，命令在执行 Python 前退出；已改为 `uv run --python 3.12 python -c ...` 成功验证。P1-C 仍等待真实 URL、Key 和模型填写后执行外部 HTTP 验收。

## 2026-08-03 — P1-C 真实服务验收

- **Status:** in_progress
- 用户确认已填完 Reranker 配置。安全存在性检查确认 URL、API Key、模型均非空；不输出配置值或凭据。
- 下一步新增并执行环境门控的 `HttpResearchReranker` 真实 HTTP 专项，验证协议响应、有效下标、分数与中文样本的语义排序；不重复此前超过外层时限的完整 RAG 长链路。
- 规划文件首个追加补丁使用了不存在的二级标题锚点，未写入任何内容；已改以当前文件的实际段落锚点更新，不重复同一补丁。
- 首次真实 Reranker 专项在 5.3 秒内失败：URL 是 SiliconFlow `/v1` 服务根路径，HTTP 返回 404；复核还发现 API Key 与模型变量填反。已将本地配置纠正为完整 `/v1/rerank` 路径与正确变量映射，不显示或记录凭据；下一次将以不同的有效配置重跑。
- 真实专项复跑通过：`RUN_LIVE_RERANKER_TESTS=1 uv run --python 3.12 pytest tests/integration/test_live_reranker.py -m live -s -q` 返回 `1 passed in 2.14s`。测试直接调用 `HttpResearchReranker`，不写入本地基础设施，验证中文语义排序、返回下标和有限分数。
- 已同步 `README.md`、`05`、`06`、`11` 号讨论稿以及两份 `.env.example`：Reranker 现为真实服务已验收状态，示例明确要求完整 `/rerank` URL 与正确的 Key/模型变量位置。
- Research Worker 已重启并加载新设置；Worker 启动路径确认只有启用状态才实例化 `HttpResearchReranker`。
- P1-C 全量后端回归通过：Ruff format/check、Pyright（0 errors）和 pytest（`177 passed, 15 skipped in 12.29s`）。P1 全部子项已完成，待最终工作区一致性检查后收口。
- `git diff --check` 通过，只有既有 CRLF 预告警。P1-C 已收口；规划完整性检查仍显示早期 Phase 8.5 为进行中，其剩余项是 Worker 重启、模型/向量瞬时失败的独立真实故障注入验收，不是 Reranker 缺口，也未在本次伪标为完成。

## 2026-08-03 — 完整 RAG + Reranker 真实链路复验

- **Status:** in_progress
- 用户要求进行完整真实链路测试。已将既有 `test_live_research_e2e.py` 收紧为必须启用真实 Reranker，且只在 retrieval trace 明确记录 `completed/http_reranker`、有效候选数与返回数时通过；随后以更长外层时限运行一次，不重复此前未配置 Reranker 的超时基线。
- 真实完整链路通过：`RUN_LIVE_RESEARCH_E2E_TESTS=1 uv run --python 3.12 pytest tests/integration/test_live_research_e2e.py -m live -s -q` 返回 `1 passed in 165.34s`。trace 为 `single_rag`，Qwen embedding 与 Milvus 返回 2 条候选，`http_reranker` 完成 2/2 精排，DeepSeek 回答后 4 条主张均获支持，Redis 写入 7 个事件；测试 `finally` 删除临时 PostgreSQL、Milvus、Redis 和 checkpoint 资源。
- **Status:** complete

## 2026-08-04 — Phase 19 浏览器验收续办

- 读取当前计划和执行记录后检查临时计划 `9df3c55a-f808-4df5-91c6-03ac856ab11c`：PostgreSQL 当前为 `ready`、有模型快照、无错误；Redis 中有对应 ARQ result（TTL 仍在），没有任务键、in-progress 键或工作流队列条目。由此确认前一轮 `generating` 是短暂的正常 Worker 执行窗口，不重启 Worker、不补投递任务，直接回到浏览器继续计划确认。
- 已用保留登录态的 `sleep-e2e` 浏览器重新打开该工作区路由；真实页面加载成功。Playwright CLI 已生成新的页面快照路径，下一步按该快照的控件继续确认计划；不以旧 `generating` 页面元素继续操作。
- 该次重载实际触发登录重定向，说明浏览器临时认证状态已失效；登录页面保留工作区 redirect 参数。已通过页面控件重新填写隔离测试账号凭据，尚未提交，下一步验证登录后是否回到同一 `ready` 计划。
- 按登录表单提交后路由恢复至同一临时工作区；真实页面展示 `READY`、计划 v1 和 3 个模型方向。已视觉核对默认选中的“睡眠障碍与抑郁/焦虑的关联及双向机制”与请求匹配，准备在保持该默认方向的前提下确认检索。
- 已滚动核对确认页的完整范围和操作边界：2018–2025、中文/英文、多个文献来源，以及“确认并开始检索”按钮均可见。保持模型默认方向和范围不变，准备由真实前端提交确认。
- 已在真实页面点击“确认并开始检索”，并进入检索运行 `d466bb54-6ae2-44a5-879a-5fa3e8c721cd`。页面最终显示 50 篇候选、处理轨迹全部完成，OpenAlex/Crossref 各 25 条；Semantic Scholar 超时且被单独标记为未返回，整体为“部分完成”，可继续进入候选审核。
- 已通过“开始筛选 50 篇文献”进入候选审核页。页面的来源、合并计数和准备清单状态与检索运行一致；已核对首项无摘要时被明确标为“信息不足”，不显示关键词伪理由。下一步使用页面的开放获取筛选，选择实际可准入的全文候选。
- 已通过“开放获取”筛选得到 25 条候选。页面显示首个开放获取项目的评估因一次相关性批量上限而未完成；待查看的睡眠指南条目带 DOI。下一步打开其检查器，不将题名或开放获取标签直接当作已准入结论。
- 已从列表选中失眠指南候选，检查器正确切换到其完整题录。该条仍被服务端标记为“分析未完成”（50 条候选超过单次 24 条评估上限），没有错误显示为已评估。下一步滚动查看检查器中的全文准入状态和操作。
- 已在检查器底部确认有“准备单篇核验”入口，并确认准备清单/长期文献库的准入提示符合后端边界。下一步触发这篇有 DOI、开放获取标记的失眠指南的真实全文核验，不先把它强制加入集合。
- 已触发真实单篇题录与全文核验。页面返回“单篇题录与全文核验已安排”，并将按钮替换为“查看完整记录”，避免重复投递；后续观察核验完成后的全文状态与可否加入准备清单。
- 等待约 15 秒并刷新可见状态后，页面仍只显示已安排/查看完整记录，没有将异步任务过早标为成功。下一步进入完整核验记录读取服务端阶段与结果。
- 已从“查看完整记录”进入候选 `01e19551-9726-4378-9556-53f822ce6964` 的完整核验页。Playwright 报告 2 条控制台错误，尚未判断是否与全文准入有关；下一步同时读取页面和控制台文本，不能仅据错误计数推断任务失败。
- 核验页题录信息加载成功；控制台错误已定位为同一候选引用格式 API 的两次 HTTP 409，不是全文请求。先记录为独立回归问题，并继续滚动查看全文状态/入库操作，避免错误归因。
- 详情页底部显示完整摘要，但研究集合状态仍是“尚未进入”，并清晰声明 DOI 不等于可研究论文。正式引用预览显示暂时不可读取，符合 citation 409。下一步返回候选页检查单篇全文核验的实际终态。
- 返回候选页后筛选恢复为全量、检查器恢复默认首项，准备清单仍为 0；这只表明前端焦点没有跨详情页保留，不能代表后台核验取消。改用“全文已核验”筛选读取服务端快照的真正终态。
- “全文已核验”筛选真实返回 0 条。集合构建和研究对话在此前提下不应继续伪造成功；下一步以相同 run 的服务端短期快照查明单篇核验的终态、错误码与可恢复路径。
- 已读取相同 run 的 Redis 候选快照：睡眠指南有 DOI、Wiley PDF 链接和开放获取来源标记，但没有正式引用，相关性 Agent 明确因候选总数 50 超过 24 上限失败。候选快照不含全文核验终态；下一步查专用全文状态键而不把链接当作成功。
- 首次读取候选专属全文状态键时，Python 向 PowerShell 默认 GBK 控制台输出包含扩展拉丁作者名的完整 JSON，触发 `UnicodeEncodeError`；查询和 Redis 未被修改。后续命令将先设置 UTF-8 控制台编码，并只输出状态、错误码和时间等最小字段。
- UTF-8 重试读取成功：全文状态为 `failed`，Wiley PDF 下载返回 `HTTP 403`、`remote_error`、不可重试；题录状态已为 `ready`。说明本轮无已验证全文的根因是上游来源拒绝自动下载，正确恢复路径是授权上传或改选文献，不应继续构建集合/RAG。
- 为核对前端失败呈现而直接导航至候选详情时，Playwright CLI 创建了新浏览器进程，未继承临时登录存储并被路由守卫重定向至登录页；这与先前同类会话重建一致。将重新登录并由 redirect 返回详情页，继续核对 `HTTP 403` 的用户恢复界面。
- 重新登录后 redirect 成功回到同一候选详情页；控制台仅复现先前已定位的 citation 409。下一步滚到详情的核验区域，确认 `remote_error/403` 是否被前端转换为授权上传或改选文献的恢复操作。
- 详情页滚动到底只显示“尚未进入研究集合”，没有显示服务端已确认的 `failed/remote_error/HTTP 403` 与恢复操作。此为真实前端可观测性缺口；下一步阅读页面与 API 状态映射，必要时以最小改动修复并回归。
- 首轮修复后的定向检查未进入测试主体：`citation_service.py` 漏导入 `SearchRun` 且需格式化；引用服务单测的 `_fulltext_state()` 被补丁插入到 `_candidate()` 的中间，造成 `source_record` 未定义。将按实际行号恢复函数结构、格式化后重跑，不改动行为设计。
- 精确整理后 Ruff 格式和规则检查通过，已有两条引用服务测试通过；新增回归的测试夹具将终态 `failed` 构造成未携带错误对象，Pydantic 契约按预期拒绝。补上既有稳定全文错误码后重跑，前端检查尚未启动。
- 已完成精确修复：后端正式引用服务在主候选题录非 `ready` 时安全读取同一运行/候选的全文状态题录；前端详情页对 `failed/rejected` 显示 `presentFulltextVerification()` 的真实说明，并对不可重试失败开放已有授权上传 PDF 路径。新增后端引用状态回归与浏览器详情页 403 回归。
- 定向验收通过：后端 Ruff format/check 与 3 个引用服务单测；前端 Prettier、vue-tsc、ESLint；`workflow-shell.spec.ts` 5 passed（包含“详情页为不可重试的全文失败提供授权上传恢复路径”）。下一步重启 API，在真实浏览器核对 citation 409 已消失及 403 恢复入口。
- 当前 API 由自动重载开发进程监听，代码改动后监听 PID 已切换。真实浏览器 `reload` 后已确认前端 403 恢复操作显示；但 citation endpoint 仍为 409。下一步绕过 HTTP 层直接调用当前源码的服务，用同一真实 run 查明是 API 进程未重载还是服务读取仍有遗漏。
- 直接调用当前源码的 `CandidateCitationService` 并使用同一真实工作区/运行/候选已成功渲染 241 字符引用，确认 409 仅为常驻 API 未加载代码。首次按端口 PID 父链定位时，监听 PID 在同一快照期间已替换而不可读取；未终止任何不确定进程，改为按项目目录搜索 API 启动父进程。
- 复核宿主进程、Docker 和 WSL 后，`127.0.0.1:8000` 仍响应 Uvicorn，但三个瞬态监听 PID 均无法由 Windows 进程表安全归属；Docker 仅承载 PostgreSQL、Redis、Milvus、MinIO 与 etcd，WSL 默认发行版也没有 API 进程。为避免误终止三个独立 ARQ Worker，后续将以独立端口启动当前源码 API，并启动临时前端显式指向该端口，完成浏览器验收后再精确停止临时进程。
- 环境检查：`npx` 可用，路径为 `E:\\nodejs\\npx.cmd`。一次 `rg` 使用 Windows 不支持的 glob 语法（`frontend/.env*`、`frontend/vite.config.*`）退出失败；已确认实际 API 基址定义在 `frontend/src/api/client.ts`，后续命令将对目录使用 `--glob`，不重复该形式。
- 隔离 API 已从当前 `backend` 源码启动于 `http://127.0.0.1:8001`，`/openapi.json` 返回 `200` 与 `Academic Search API`。隔离 Vite 已启动于 `http://127.0.0.1:5174`，仅通过进程环境变量 `VITE_API_BASE_URL=http://127.0.0.1:8001` 指向新 API，未改动产品配置。
- Playwright `sleep-e2e` 会话新增了 `5174` 标签页；浏览器共享 Cookie 但本项目令牌保存在按 origin 隔离的 localStorage，因此页面按预期显示登录表单，而不是错误地复用 `5173` 的前端状态。下一步将以不输出令牌的方式把同一临时会话的测试存储迁移到隔离 origin，再访问原候选详情页复验。
- 首次切换 Playwright 旧标签页的 `tab-select 0` 在 34 秒超时，未依据该命令假定是否实际切换，也不重复同一条指令；将改为不依赖标签切换的会话存储状态路径。
- 替代性 `tab-list` 同样在约 34 秒超时，表明 `sleep-e2e` 会话本身已卡住，而不只是标签切换。按三次错误协议，后续将停止使用此会话，使用无交互状态令牌初始化一个新的 Playwright 会话；此前真实注册/登录证据仍保留，新的会话只用于新 API 代码的详情页回归。
- 已为同一隔离测试账号按当前应用的正式 JWT 签发逻辑生成短生命周期临时访问令牌，并仅写入 `output/playwright/.sleep-e2e-token`；未读取、输出或记录令牌内容。旧会话没有可杀死的 daemon，随后以 `sleep-e2e-isolated` 新建有头浏览器，并确认它在 `5174` 隔离 origin 的正常登录页。下一步仅把该临时令牌写入前端使用的同一 localStorage 键后刷新，令牌文件将在验收清理阶段删除。
- `sleep-e2e-isolated` 的显式 `localstorage-set` 也在约 34 秒超时，且没有返回令牌内容或页面状态；不重复此会话管理指令。将改为在已有新鲜页面快照的同一 Playwright 上下文中一次性设置前端状态，再从 UI 发起后续请求。
- 为避免令牌出现在 CLI 参数或输出中，创建了只含同一临时令牌的 Playwright storage-state 文件；不同的 `state-load` 会话管理指令仍在约 34 秒超时。结合此前 `tab-select`、`tab-list` 和 `localstorage-set` 的同类超时，已停止对该 CLI 会话状态通道的重试，不将其误判为产品前端错误。后续改用新 Playwright 会话，在 `5174` 通过注册 UI 创建新的隔离账号，并从真实前端重新提交相同研究要求；届时不会依赖或迁移任何令牌文件。
- 新的 `sleep-e2e-ui` 会话连 `open` 都在约 34 秒超时，确认故障已升级为 Playwright CLI 运行时问题，而非页面、鉴权或 API 问题。按既定恢复策略，下一步只定位并结束带有 Playwright CLI 用户数据目录的 Chromium 子进程，不影响用户常规浏览器或项目 Worker；之后才会尝试一次全新会话。
- 已精确定位卡住的测试守护进程 `cliDaemon` 及其 `playwright_chromiumdev_profile` Chromium 树，并仅结束该守护及一个已超时的 CLI 调用；确认它们的进程 ID 不再存在，未影响项目 API、三个 ARQ Worker、Docker 基础设施或用户常规浏览器。新会话 `sleep-e2e-ui-2` 成功打开 `5174/register`，真实注册表单包含显示名称、邮箱、密码和创建账号操作。下一步从该 UI 创建新的隔离账号并走同一研究请求。
- 已通过真实注册页面填写新的隔离账号显示名称与邮箱；尚未提交，因此数据库还没有新账号或工作区副作用。下一步填写仅用于本轮验收的短期密码并从同一 UI 提交注册。
- 注册提交没有创建账号：浏览器明确报出 `http://127.0.0.1:5174` 到隔离 API `http://127.0.0.1:8001` 的 CORS 预检未返回 `Access-Control-Allow-Origin`，页面显示 `Failed to fetch`。这是隔离测试端口不在默认开发允许源的联调配置缺口，不是认证逻辑失败；将只以进程环境变量把 `5174` 加入隔离 API 的允许源，随后重启该临时 API 并重试同一未落库表单提交。
- 已停止并重启唯一的隔离 `8001` API，保持源码和持久化基础设施不变，只在该进程的 `CORS_ALLOWED_ORIGINS` 追加 `http://127.0.0.1:5174`。实际 `OPTIONS /api/v1/auth/register` 预检现为 `200`，明确返回该 Origin 与完整允许方法。浏览器刷新后注册页恢复为干净表单、无前次 `Failed to fetch` 提示；下一步重新填写并提交隔离账号。
- 重新填写显示名称和邮箱成功；由于前一次请求被浏览器预检拒绝，邮箱没有被创建，可以继续使用同一隔离地址。下一步填写短期密码并提交注册，随后立即从重定向页面提交研究要求。
- 注册已通过真实 `5174 -> 8001` 前端请求成功，浏览器重定向到研究入口并显示当前用户“睡眠研究验收”。页面提供“研究要求”输入框与“开始分析”按钮，确认 API、CORS、注册和前端鉴权状态在隔离进程上均可用。下一步填写同一睡眠与心理健康研究要求并从 UI 开始分析。
- 已从真实研究入口提交“我想研究睡眠质量与心理健康之间的关系”。前端创建独立工作区 `6a8d32a9-0d2f-4e51-9620-06c0d5c2d4a1` 并进入 `run` 页面，当前展示计划 v1 的 `RUNNING` 解析状态、原始问题及三步任务轨迹；没有前端伪造计划结果。下一步等待 Worker 完成 DeepSeek 意图分析，再由页面确认模型方向与范围。
- 约 15 秒后，真实 DeepSeek 意图分析完成并在页面返回 3 条候选路径。默认选中“睡眠障碍与心理障碍的共病及机制”，内容覆盖睡眠质量与抑郁/焦虑关联、内分泌、遗传与炎症机制；另外两条为睡眠干预效果及真实情境的情绪动态。页面真实显示 `2018–2025`、中文/英文、OpenAlex/Crossref/Semantic Scholar 与固定 DOI/正式题录/可处理全文准入规则。该默认路径匹配用户问题，保持不变，下一步点击“确认并开始检索”。
- 已由页面确认计划并启动检索运行 `7ca1381e-0776-4c4e-884e-1120816f8f45`。约 25 秒后真实页面显示 50 篇可审核候选并标记“部分完成”：OpenAlex、Crossref 均返回 25 条，Semantic Scholar 请求超时但不影响已返回记录的规整与初筛。处理轨迹完整结束；相关性 Agent 显示已分析 12/50，38 篇需重试，这一批次级限制被如实展示而未伪造理由。下一步从“开始筛选 50 篇文献”进入审核。
- 已经从真实页面进入候选审核。页面维持“准备清单”“待确认集合”“可研究集合”的边界：当前准备清单与待确认集合均为 0，不把 DOI 或开放获取标签当成准入成功。候选列表明确呈现无摘要的“信息不足”和超过 24 条相关性上限的“分析未完成”，并提供“准备单篇核验”和“查看完整记录”。为复验正式引用修复与全文失败恢复路径，下一步打开高度相关的 `Sleep, insomnia, and depression` 候选详情。
- 已从列表打开 `Sleep, insomnia, and depression`（Neuropsychopharmacology，2019，DOI `10.1038/s41386-019-0411-y`）的真实详情页。页面准确显示来源缺少摘要、正式引用尚待题录核验，未提前生成引用。已由“准备全文核验”从页面投递单篇异步题录/全文任务；下一步等待任务终态，检查题录就绪后的正式引用与全文准入状态。
- 单篇异步核验真实完成，详情页显示“已通过核验：题录与可处理全文均已就绪，可以加入待确认集合”。页面在同一挂载周期仍保留核验前的“题录尚未核验完成”引用占位，符合查询在任务启动时失败后的缓存行为；已刷新同一真实详情页，以当前隔离 API 的新代码重新请求正式引用接口。下一步读取刷新后的引用预览与浏览器控制台，不把刷新前的旧占位当作服务端修复失败。
- 刷新后的真实详情页已成功显示 GB/T 7714-2015 正式引用预览（含作者、题名、期刊、年份、卷期页码与 DOI），并出现引用格式选择和复制操作；先前 HTTP 409 不再出现。页面同时保留“已通过核验：题录与可处理全文均已就绪”的准入状态，验证 `CandidateCitationService` 的全文状态题录回退修复在常驻的当前源码 API 上生效。下一步由“前往核验任务加入集合”继续真实集合构建，而不直接写库。
- 从详情页进入核验任务后，页面显示本次准备清单 1 篇、可加入 1 篇、核验中 0、暂时受阻 0、待确认集合 0，且该候选仍是“已通过核验”。点击加入后，前端展示明确确认窗口：只有题录和全文通过核验的候选会加入，未通过项不会被批量带入。下一步通过 UI 点击“确认加入”，触发真实持久化集合边界。
- 已在确认窗口通过 UI 将 1 篇文献加入待确认集合；页面随即显示准备清单 0、待确认集合 1 和“本次已加入 1 篇文献”。集合页真实显示活动文献 1、待确认构建 1、可研究 0，论文状态为“待构建”，研究对话保持锁定。下一步点击“确认并构建集合”，由 Worker 执行解析、分块、嵌入和 Milvus 索引。
- 已从集合页确认构建，并在约 35 秒后看到真实终态：活动文献 1、待确认构建 0、可进入研究 1；`Sleep, insomnia, and depression` 已显示“可研究”，研究对话由锁定变为已解锁。该结果表明实际 Worker 已完成解析、分块、embedding 和 Milvus 索引，下一步进入研究页，用与当前单篇范围相符的问题验收受限检索、Reranker 和可定位证据呈现。
- 已进入真实研究页：页面明确将范围限制为当前集合中 1 篇已核验、已索引全文，并承诺证据不足时不以模型记忆补全。已在 UI 输入“根据当前文献，失眠和抑郁之间的关系及可能机制是什么？”，该问题与唯一已索引论文范围相符。下一步通过“发送研究问题”投递真实异步 RAG 运行，随后等待结果和可定位证据。
- 研究问题已由真实 UI 提交并创建会话 `026ff66b-83e9-41b2-82bd-4819ac409fdf`。约 50 秒后，页面没有伪造答案，而是清楚显示“研究模型调用失败，未生成可核验回答”、耗时 22.3 秒、已确认 0 个候选证据片段与可见“重新投递”操作。下一步从 PostgreSQL 读取该运行的稳定错误码、阶段 trace 和 Reranker 记录，定位故障原因后再决定是否重试。
- PostgreSQL 审计显示该研究运行为 `78a9c53b-ed16-4cbd-be07-196b907e1b56`，`status/stage=failed`、`error_code=research_model_failed`。时间线包含 `preparing` 11.4 秒与 `hybrid_retrieval` 10.9 秒、总耗时 22.3 秒，随后在模型结构化输出阶段失败；不是集合边界、Milvus、embedding 或 Reranker 的明确错误。下一步读取模型适配器和 Worker 可获得的错误证据，找出无法生成可核验回答的具体原因，之后才使用 UI 的“重新投递”。
- 直接用当前真实 DeepSeek 配置重放结构化路由调用，稳定复现具体原因：网关返回 `{"reason": "…", "agent": "multi_agent"}`，语义与契约一致但字段名为 `agent`；`ResearchRouteDecision` 当前只兼容 `mode`、`router`、`choice`，因此 Pydantic 报缺少 `mode` 并被安全转换为 `research_model_failed`。这不是模型、embedding、Milvus 或 Reranker 故障。下一步以最小改动将 `agent` 加入唯一别名白名单，并补回归测试后，从真实 UI 重新投递同一运行。
- `agent` 白名单映射及单测已通过 Ruff 与研究图单测（`10 passed`）。不过同一真实路由调用随后返回另一种已观察到的同义字段 `route: "single_rag"`，再次因缺少 `mode` 被严格拒绝；这表明兼容层需覆盖同一网关的已观察字段漂移，而不是放宽稳定模式集合。下一步将 `route` 加入该唯一白名单和参数化回归，再做模型探针，成功后重启唯一研究 Worker 并从 UI 重投。
- 已将 `route` 追加到路由字段的唯一白名单，并把参数化契约回归扩展为 `router`、`choice`、`agent`、`route` 四种已观察别名。Ruff format/check 均通过，研究图单测为 `11 passed`。同一真实 DeepSeek 路由探针现成功返回 `mode=single_rag` 和用户可理解的理由，未扩大稳定模式集合。下一步仅重启研究 Worker 让运行时加载修复，然后从 UI 的“重新投递”继续同一问题。
- 已精确终止旧研究 Worker 的进程树并启动新的独立 `arq app.workers.research.WorkerSettings` 进程，确认新树的启动根 PID 为 `22156`。从真实页面点击“重新投递”后，等待 70 秒页面仍显示“已连接研究进度流 / 问题已保存，正在投递研究任务”，没有新失败也没有进入检索阶段。下一步读取该重试运行的 PostgreSQL 状态、ARQ job ID 和 Redis 队列，判断是任务未被领取还是投递状态未刷新，避免重复点击重投。
- 重试运行在 PostgreSQL 中为 `queued/dispatch`、无错误，ARQ queue ZSET 却为空。定位到根因：`retry_run()` 重置数据库的 `arq_job_id`，但 `ArqResearchJobQueue` 始终使用 `research-{run_id}` 作为 `_job_id`；旧失败 job 的 Redis 结果仍存在时，arq 返回 `None` 表示幂等重复，适配器把它误当作投递成功，导致无队列任务的永久 queued。下一步首次投递继续使用稳定 job ID，而 `retry_run()` 显式请求一个新的 ARQ job ID；补回归测试后，以新的研究问题完成浏览器 RAG 验收。
- 已实施重试投递修复：首次任务仍用 `research-{run_id}` 保持幂等；只有 `retry_run()` 显式传入 `retry=True` 时才生成 `research-{run_id}-retry-{uuid}`，不会被旧 Redis 结果键吞掉。新增 `test_research_job_queue.py`，模拟 Redis 并验证两个 ID 不同；两次真实专用 Worker 替身更新了新方法签名。初次 Ruff 仅报新测试导入排序，已由 Ruff 自动修正；最终 Ruff format/check 通过，`test_research_job_queue.py + test_research_graph.py` 为 `12 passed`。下一步做类型检查、重启临时 API/研究 Worker，取消旧 queued 运行后从 UI 创建新问题验收。
- 定向 Pyright 通过（0 errors）。已精确重启临时 `8001` API（保留 `5174` CORS 允许源）和研究 Worker，使队列重试与路由兼容修复均进入运行时。浏览器显示的两条 console `network error` 发生在服务重启造成的 SSE 连接中断窗口；随后从真实 UI 点击旧 queued 运行的“取消任务”，不直接改写业务状态。下一步确认取消终态、发起新的研究问题（新 run ID）来验收完整 RAG。
- 旧 queued 运行已从真实 UI 正常进入“已在安全执行边界停止，不会生成回答或新的引用证据”终态。随后在同一会话中输入并提交新的集合受限问题“根据当前文献，睡眠、失眠与抑郁之间的关联是什么？”，创建了新的 RAG 运行而非重复旧 job ID。下一步等待新 Worker 的完整模型、检索、Reranker、答案和证据核验终态。
- 等待约 100 秒后，新运行没有失败或悬挂：真实页面已进入“正在逐项核验回答主张是否被实际引用的原文支持”，并显示已确认 4 个候选证据片段和可用的协作停止操作。这确认修复后的路由解析、集合受限混合检索和后续 Reranker/回答链路已推进到最终证据核验阶段。下一步继续等待终态，再核对回答文本、证据展开与原文定位。
- 继续等待约 80 秒后，新运行以正常 `awaiting_clarification` 终态返回，而非 `failed` 或 queued：页面展示结构化路由理由（单一综合查询）、模型预算 `3/16`、检索预算 `1/6`、耗时 239.2 秒，并明确说明“当前检索到的原文无法完整支持准备输出的结论。请缩小问题范围或补充相关文献。”这证明路由、混合检索、真实 Reranker、回答草稿与主张核验均可执行；当前单篇集合的 4 个候选证据不足以完整支撑该宽泛问题，系统正确拒绝编造回答。下一步读取运行 trace 中的 Reranker 和证据计数，保存浏览器截图并清理本轮临时资源。
- PostgreSQL 审计确认最新运行 `a6dcab0c-39f9-4d2f-9d6b-fe984bb4a5b1` 为 `awaiting_clarification`、`single_rag`、无错误码；真实 Reranker trace 为 `http_reranker/completed/enabled`，14 个候选中返回 6 个，最终证据 6 个。回答主张核验检查 11 条主张，发现 1 条不被充分支持并将 outcome 安全标记为 `clarification`。已用 Playwright 截图并复制到 `output/playwright/sleep-rag-awaiting-clarification-20260804.png`。下一步按精确 UUID 与邮箱盘点并清理两个隔离账号的 PostgreSQL、Redis、MinIO、Milvus 与 checkpoint 资源，绝不触碰现有用户资源。
- 清理盘点已确认只涉及两个精确隔离账号：旧账号 `005645d0-6c20-47fc-a061-4ecb37625a92` 的工作区 `cb21915b-36fe-4de2-838b-6fd96db119c3` 无长期文档；新账号 `f1560a9d-5551-4fd5-a8c4-b34066fcfbbf` 的工作区 `6a8d32a9-0d2f-4e51-9620-06c0d5c2d4a1` 有 1 个文档、1 个入库运行、2 个研究运行和 1 篇仅供本轮资源引用的论文。一次用于查找存储适配器的组合 `rg` 因第二个无匹配子查询退出 1，但已用独立查询定位 `Boto3StagingObjectStorage` 与 `MilvusDocumentChunkIndex`；不重复该组合命令。下一步按上述精确范围先删除对象/向量、再删除 Redis/checkpoint/PostgreSQL，并验证所有计数为零。
- 已执行精确清理：仅匹配两条隔离邮箱、关联工作区/运行 UUID 与两个候选 ID。删除 2 个用户、2 个集合、1 个 MinIO 文档对象、1 个 Milvus 入库运行向量、13 个 Redis 键，并在集合删除后删除无其他引用的测试论文。数据库复核（用户、集合、文档、入库运行、研究运行）均为 0；Redis、MinIO 和 Milvus 精确前缀/运行查询也均为 0。下一步停止临时 `8001` API、`5174` Vite 与 Playwright 会话，删除临时浏览器令牌文件，并做最终 diff/测试状态检查。
- 已停止本轮隔离 Vite `5174` 和 `sleep-e2e-ui-2` Playwright 守护进程；精确删除临时 JWT、浏览器 storage state 与含临时密码的 Playwright 页面快照，并逐项复核文件不存在。常驻 `8000` API 与研究 Worker 未被终止。
- 最终后端校验：`ruff format --check`、`ruff check` 与 `pytest -m "not live"` 通过（`179 passed, 17 deselected`）；按后端 README 的 `uv run pyright` 复核为 `0 errors`。直接以 `python -m pyright` 运行时未自动发现 `.venv`，误报 208 个第三方导入缺失，故未将其视为代码问题。
- 前端首次以 Codex 内置 Node `24.14.0` / pnpm `11.9.0` 执行失败，仓库正确拒绝未满足 `package.json` 引擎范围的运行时。已确认项目锁定的 Node `20.19.6` 位于 `E:\nodejs\node.exe`，后续将使用该 Node 的 Corepack 调度 pnpm `10.34.5`，而不绕过版本约束。
- 已以 `E:\nodejs\node.exe` 的 Corepack 调度 pnpm `10.34.5`，确认 Node `20.19.6` / pnpm `10.34.5` 匹配项目锁定范围；前端 `format:check`、`lint`、`typecheck`、`test:unit`（`19 passed`）与 `build` 全部通过。`uv run alembic check` 报告无新迁移，最终 `git diff --check` 通过。Phase 19 现已完成。
- 用户请求重启项目。已新增 Phase 20；当前 Docker 状态服务均健康，Vite 监听 `5173`、API 监听 `8000`。下一步先精确定位 API 与 Worker 的进程树，再按标准开发拓扑重启并检查。
- 已精确停止三类旧 ARQ Worker 和旧 Vite，Docker Compose 已重启 PostgreSQL、Redis、etcd、Milvus 与 MinIO，五项均恢复 `healthy`。Windows 将旧 `8000` API 报为多个无法安全归属的监听 PID，不能按端口盲杀；为避免误伤，保留其已通过的健康检查，并以当前源码启动可管理的 FastAPI `8002`。
- 工作流、入库与研究 Worker 已重新启动；Vite 已在 `http://127.0.0.1:5173` 恢复。健康检查结果：`5173` 返回 200，`8000/healthz` 与 `8002/healthz` 均返回 200，`8002` 对 `5173` 的 CORS 预检返回 200 且允许该 Origin。两次尝试在 Vite 进程级注入 `8002` 基址均被本地执行策略拒绝，因此未修改前端配置；无 `frontend/.env` 时，当前页面继续按源码默认访问健康的 `8000`。Phase 20 完成。
- Phase 21 已完成。依据讨论稿，删除 `WORKFLOW_RELEVANCE_COLLECTION_MAX_CANDIDATES` 和容量失败分支；完整候选集合仍由一次相关性 Agent 调用判断，随后一次完整集合的独立主张核验。相关性输出预算改为 `700 * 有摘要候选数`，核验预算改为 `128 * 有摘要候选数`，不引入候选级或批次级串行调用。
- 验证：Ruff、Pyright 为零错误；候选相关性、搜索执行和单项重试定向测试 `14 passed`，后端非联网全量回归 `179 passed, 17 deselected`。真实 `deepseek-v4-flash` 验收以 50 条临时内存候选完成一次完整判断和一次完整独立核验，结果 `50 completed`、无错误码、无 PostgreSQL/Redis/对象存储写入；一次先前模型响应的结构化输出异常被安全拒绝，随后诊断确认原始 50 项 JSON 可解析并复验成功。
- 最终旧配置审计首次命中 `findings.md` 与 `progress.md` 中的历史排障事实；保留这些审计记录后，将检查范围收敛到运行代码、测试、环境模板与正式文档，确认旧候选数量限制、容量失败码与固定 2400 token 配置均已移除。`git diff --check` 通过。
- 在最终回复前复核到仍有 `WORKFLOW_RELEVANCE_ABSTRACT_MAX_CHARACTERS=3000` 的单篇摘要截断。它不造成串行，但用户已明确在 1M 上下文模型下不保留人为候选限制；新增 Phase 22，将移除该截断，使完整摘要与完整候选集合一并进入单次共享上下文调用。
- Phase 22 已完成：删除 `WORKFLOW_RELEVANCE_ABSTRACT_MAX_CHARACTERS` 设置、模板项和摘要切片，相关性 Agent 现在接收完整候选集合中的完整摘要。单元测试改为断言完整摘要保留；全量后端检查通过（Ruff、Pyright，`180 passed, 17 deselected`），工作流 Worker 已重启。运行时设置确认不再含摘要上限，正式代码、模板、测试与讨论文档均无旧限制引用，`git diff --check` 通过。
- 用户指出 1M 上下文模型下不应由候选数量触发相关性分析阻断，并强调不能因此退回此前的串行批次行为。已新增 Phase 21，初步检索发现讨论稿要求“完整候选集合一次进入共享上下文”，并明确禁止隐式串行分批；下一步读取原始段落与当前执行实现，先给出对齐结论，不擅自修改。
- 2026-08-04 复读 `03`、`11` 讨论稿与当前执行代码：当前相关性流为一个 `eligible` 集合的一次评估调用，成功后为同一集合一次独立主张核验调用；全文下载、PDF 校验、准入和向量化才是逐篇任务。运行代码、环境模板和测试均未发现旧 24 条数量阈值、摘要截断或隐式串行分批。当前浏览器页面所示上限提示来自历史 Redis 快照，不能靠刷新回填，需要新检索运行验证。
- 计划记录补丁第一次因 `apply_patch` 空 hunk 被拒绝；已改为锚定现有段落的三文件补丁并成功写入。下一步重跑集合级测试并检查当前工作流 Worker。
- 集合级回归已通过：`test_candidate_relevance.py + test_search_execution.py` 共 `13 passed`，其中断言 50 条候选只进入一次统一评估调用。运行时设置为相关性 `700 * candidate_count`、核验 `128 * candidate_count`，不存在候选数或摘要长度属性；工作流 Worker 进程树正常，`5173` 和 `8000/healthz` 均返回 `200`。Phase 23 完成，无需新增实现限制。
- 用户请求重启项目，已新增 Phase 24。首次从项目根执行 `docker compose ps` 报“no configuration file provided”，表明 Compose 文件不在根目录；`5173`、`8000` 和 `8002` 均仍有监听。下一步先用命令行、父子关系和实际 Compose 文件位置精确归属进程，避免按端口盲杀未知服务。
- 已定位 Compose 实际文件为 `infra/compose/compose.dev.yml`，并用容器标签确认五项基础设施均归属本项目。精确终止了 Vite、`8002` API 和三类 Worker 的已验证进程树；未触碰 Windows 无法归属的旧 `8000` 监听。Docker 容器重启后全部为 `healthy`。
- 本地执行策略拒绝 `Stop-Process` / `Start-Process`，改为精确 `taskkill /T` 和无窗口 `cmd start /b`；该替代方式成功。首个 Vite 启动工作目录错误并返回 `404`，已停止并从 `frontend` 目录重启。最终验证：`5173`、`8000/healthz`、`8002/healthz` 均为 `200`，`5173 -> 8002` CORS 预检为 `200`，三类 Worker 启动根均在运行，Vite 转换的客户端模块包含 `http://127.0.0.1:8002`。Phase 24 完成。
- 用户报告当前搜索运行 `70e88d14-7a6b-4467-b31d-e4109d584010` 的候选相关性均失败，已新增 Phase 25。源码复核表明失败可能来自首次相关性模型返回无效/不可用，或后续独立主张核验失败；候选数量不再是失败分支。首次环境键探测错误假定 `backend/.env` 存在，实际配置由项目根 `.env` 加载；不重复该错误路径，下一步读取数据库审计和 Redis 快照取得具体错误码。
- 运行审计确认：50 条原始候选中 49 条通过初筛，25 条相关性完成、24 条失败；23 条失败码为 `candidate_relevance_model_unavailable`，1 条为独立主张核验拒绝。相关性阶段耗时约 45.3 秒，恰好命中当前 `WORKFLOW_RELEVANCE_TIMEOUT_SECONDS=45`；Semantic Scholar 的来源超时只导致运行状态为 `partial_failed`，不是相关性批量失败根因。
- 用户要求先验证流式可行性而非编写离线测试。已对同一完整 50 条候选输入做真实、不落库的 JSON 流式探针：第一个内容块在 77.922 秒抵达、总时长 103.172 秒、14,084 个流块拼接为 15,340 字符 JSON，并通过现有批量 schema 得到 25 条评估。流式方案可行，且直接证明 45 秒总等待会在首个流片段到达前误杀请求；尚未继续实施流式改造或重投当前运行。
# 2026-08-04 — Phase 25 流式候选相关性分析实施启动

- 已核实失败根因不是候选容量：同一 50 条候选的真实 JSON 流首个内容在约 78 秒后到达、总时长约 103 秒，而旧 `WORKFLOW_RELEVANCE_TIMEOUT_SECONDS=45` 在完整结果前中断请求。
- 已核实 ARQ `Worker` 会把函数任务放入 `asyncio.wait_for`，默认回落到全局 `job_timeout=240`；无限等待模式又需要主动续期其 in-progress 标记，否则长任务会被重新领取。
- 本轮实施目标：专用 relevance 队列与 Worker、120 秒流活动空闲监测、可续约运行租约、运行级全量重试/取消、Redis 乐观字段合并；不拆分候选集合、不截断摘要、不向前端公开模型流正文。

### 流式相关性修复续接（2026-08-04）

- 恢复 Phase 25 后已复核当前工作区：流空闲计时按每次 `anext()` 等待处理，空内容活动块同样刷新活动窗口；相关性和题录均通过 Redis `WATCH/MULTI` 合并最新快照。
- 规划文件首轮补丁因使用了已不存在的小节标题而被拒绝；随后错误把 `task_plan.md` 的尾部说明当成 `progress.md` 锚点。现已以实际的 Phase 25 记录为锚点，后续不重复这两种补丁定位方式。
- 接下来补齐启动即续约的 ARQ 占用保护、显式会话键校验和字段级合并回归，然后执行 API、前端和真实模型链路验证。

- 已补齐执行器启动即续约、显式事件会话键校验、取消前检查、空内容流块活动测试、运行级 API 契约测试和字段级合并测试；开发文档已改为三类 Worker、120 秒流空闲阈值与运行级整批重试。
- 首轮定向质量检查的测试为 `21 passed`，但 Ruff 要求 API 契约测试自动格式化，已执行格式化。随后 Pyright 指出测试替身协议把可选 `astream()` 错当成必需方法；协议改为最小 `ainvoke()` 边界并在运行时探测流接口。第二次 Pyright 仅剩测试替身参数名不匹配，已按协议修正，下一次检查不会重复旧命令组合。

- 专用 `app.workers.relevance.WorkerSettings` 已在本机启动并确认消费 `arq:queue:relevance`。对历史运行 `70e88d14-7a6b-4467-b31d-e4109d584010` 的当前 Redis 50 条候选快照执行了运行级整批重试，没有重调 Provider。真实运行在约 152 秒后完成，旧 45 秒阈值已被明确跨越：48 条完成、25 条为缺摘要的确定性信息不足、1 条 `Memory Consolidation` 被独立主张核验以 `candidate_relevance_claim_unsupported` 拒绝，故最终为可重试的 `partial_failed`，不是流超时或模型正文泄露。
- 全量后端回归首次通过 `186 passed, 17 deselected`，但 Ruff 发现一处既有格式差异，Pyright 发现测试替身快照的 `object` 索引未收窄；已最小修正并复跑为 Ruff 通过、`186 passed, 17 deselected`、Pyright `0 errors`、`alembic check` 无迁移差异。

### 流式相关性收尾验证（2026-08-04）

- 新增的流 JSON 分类通过定向质量门禁：Ruff format/check、`test_candidate_relevance.py`（13 passed）与 Pyright（0 errors）均通过。空流或 JSON 拼接失败现在稳定映射为 `candidate_relevance_output_invalid`，不再笼统显示为模型不可用，且错误信息不包含模型原文。
- 已按可归属进程树重启 `app.workers.relevance.WorkerSettings`，新 Worker 正在消费专用 relevance 队列。第一次 Windows 启动命令因双引号转义错误仅遗留一个 `cmd` 壳，未启动重复 Worker；已精确结束该壳，并改用 PowerShell 单引号传递的 `cmd start /b` 成功启动新 Worker。
- 先前 Playwright 会话 `sleep-relevance-live` 已不存在（CLI 返回 no browsers），无法复用已登录状态；后续浏览器验证会使用新的隔离会话，不访问或修改用户工作区。
- 新隔离 Playwright 会话 `sleep-relevance-final` 已在 `http://127.0.0.1:5173/login` 打开并取得新快照，登录页元素与前端服务均正常；下一步注册专用临时账号。
- 会话已通过真实页面导航至注册页并取得最新元素快照；注册表单包含显示名称、邮箱、密码和创建账号操作，未复用或读取旧会话的认证状态。
- 已在真实注册表单填写隔离显示名称和唯一普通邮箱；不在规划文件记录密码或令牌，下一步提交注册并等待服务端状态。
- 注册提交已从真实页面成功跳转到研究入口（`/`），说明隔离账号已创建并获得前端会话；下一步读取当前入口快照并提交睡眠与心理健康研究要求。
- 入口快照确认真实页面提供研究要求输入和开始分析操作；已填写“睡眠质量与心理健康、睡眠障碍、抑郁和焦虑双向机制”的实际研究要求，尚未提交检索或改写任何候选快照。
- 真实意图分析已成功生成 3 条研究主线；页面默认选中“睡眠障碍与抑郁焦虑的双向纵向关联”，检索范围为 2010–2025 年的中文、英文文献。将保留该模型生成的默认选择，提交真实多源检索。
- 真实计划确认已创建运行 `8165b932-d636-42d1-974b-f1d200b52f93`。约 24 秒后页面显示 50 篇可审核候选、OpenAlex/Crossref 返回、Semantic Scholar 超时隔离；相关性轨迹显示 21/50 已完成、29 篇可重试。该页面状态不是旧 45 秒总时长中断，需进入结果页读取服务端稳定错误分类并验证运行级整批重试。
- 真实结果页确认缺摘要候选展示为服务端确定性“信息不足”，不伪造模型理由；检查器仅提供运行级“重新分析全部候选理由”，没有单候选重试入口。将通过该运行级控制对当前 50 条快照发起一次完整重新分析。
- 从真实结果页点击运行级“重新分析全部候选理由”后，页面显示 `请求失败（HTTP 404）`，且候选状态未重置；这表明当前阻断发生在前端到 API 的路由请求层，不是流空闲、模型总时长或 Worker 执行失败。下一步读取浏览器请求和前后端路径契约，按最小范围修复后重试同一运行。
- 浏览器请求确认前端正确发送 `POST /api/v1/collections/{collection_id}/search-runs/{run_id}/relevance/retry`，源码路由亦存在；但旧 `8002` OpenAPI 缺少该路径。已精确重启该受控 API 进程，健康检查为 200，OpenAPI 已确认同时包含 retry/cancel 两个 relevance 路由；无需修改前端或 API 源码。
- API 重启后对同一 50 条候选再次点击整批重试成功。超过旧 45 秒阈值后，真实页面仍显示“正在重新分析当前完整候选集合”、具摘要候选为“正在分析”并提供运行级取消按钮；未重新请求 Provider，未显示模型流正文、未校验 JSON 或部分理由。
- 后续真实页面已出现服务端核验后的“核心相关、关联研究、背景参考、不建议优先”理由，并将不能由题名摘要支持的候选标为“分析未完成”，没有回退为关键词推测。浏览器网络记录确认第二次 retry 为 HTTP 202，之后只有运行与候选读取（200），没有新的 Provider 搜索请求；待读取最新运行响应确认终态与准确统计。
- 最新运行响应确认相关性已完成 `48/50`，其中 `21` 条为缺摘要的确定性信息不足、`2` 条因独立证据核验拒绝；运行仍处于 `running/citation_enrichment`，尚未结束题录预取。发现结果页此前只将 `relevance_assessment` 识别为活动状态，导致题录补全时过早停止轮询并错误显示整批重试；已改为以 queued/running 整体状态保持轮询和禁用重试，取消仍只在 relevance 阶段可见，并新增浏览器回归。
- 前端针对改动的 Prettier、ESLint 与 `vue-tsc` 均通过。第一次 Playwright 目标套件 6 条均失败，原因是当前真实 Vite 使用 `VITE_API_BASE_URL=http://127.0.0.1:8002`，而测试拦截器固定匹配 `8000`，导致模拟路由全部未命中；这不是新的状态机断言失败。下一步读取 Playwright 配置，以隔离的测试 Vite 基址重跑，避免影响真实浏览器会话。
- 隔离 Vite `5175` 已确认编译为 mock API `http://127.0.0.1:8000`，但环境值带有末尾空白；`apiBaseUrl` 原先只去除末尾斜杠，使请求无法精确命中 Playwright 的 `8000` 路由，六条用例均显示空状态。已将客户端基址规范化为先 `.trim()` 再去除末尾斜杠；这是配置容错修复，不改变真实服务的 API 路由。下一步复跑同一目标 E2E。
- 修复后目标 E2E 为 `6 passed`，全量前端 E2E 为 `11 passed`；格式、ESLint、`vue-tsc`、单元测试（19 passed）和生产构建均通过。`git diff --check` 通过。
- 本轮测试专用 `5175` Vite 已精确停止。随后发现开发 `5173` 不在监听，已按受控 `VITE_API_BASE_URL=http://127.0.0.1:8002` 重新启动并确认 `5173`、`8002/healthz` 均为 200，客户端编译模块指向 `8002`。Phase 25 完成。
- `playwright-cli list` 在当前 Windows/WSL 混合会话管理器中超时；未通过数据库、令牌或其他越权手段接管旧浏览器会话。真实浏览器验证已在隔离会话完成，本轮以已通过的 Playwright E2E 与最终运行审计完成收尾。
- 用户报告 `test@qq.com` 的运行 `19b46a2b-db9b-46c3-94f0-0452768e3992` 显示 41 条相关性需要重试。审计确认 Provider 三路均完成；29 条无摘要候选走确定性“信息不足”，41 条有摘要候选被同一调用标为 `candidate_relevance_model_unavailable`。Redis 事件显示相关性阶段恰好运行约 45.28 秒，且没有 relevance Worker 任务。实际 workflow ARQ Worker 自 2026-08-04 15:20 起未重启，仍在内存中执行旧的内联 45 秒超时路径；当前源码已改为独立 relevance 队列。仅完成诊断，未修改候选快照、未重投任务、未改代码。Phase 26 完成。
- 用户请求重启项目。已精确停止前端 `5173`、API `8002` 和 workflow/relevance/ingestion/research 四类 Worker 的可归属进程树，未停止健康的 PostgreSQL、Redis、Milvus、MinIO 或 etcd 容器。所有应用进程在 2026-08-04 19:06:02 以当前源码启动；`5173`、`8002/healthz` 和 `5173 -> 8002` CORS 均返回 200，workflow 与 relevance 日志确认各自任务配置已加载。Phase 27 完成。
- 用户询问新运行 `2ba55e7f-05b9-4533-bbb0-f64aa6c892ce` 为何仍有 1 条需要重试。审计确认新链路已执行约 170 秒并完成 49/50；唯一失败为 `candidate_relevance_claim_unsupported`，独立核验拒绝模型的 `helpful_aspect` 超出该论文标题/摘要可见证据。未修改候选、未放宽核验规则、未重投任务。Phase 28 完成。
- 用户请求提交本轮工作区。审计得到 97 个项目文件（源码、两条迁移、测试、文档和规划记录），无已有暂存；根目录 `.vite/` 是唯一未跟踪生成物，已写入 `.gitignore`。`git diff --cached --check` 通过，暂存差异的字面量密钥扫描无命中；已创建“完善研究工作流与候选审核”本地提交，并通过仓库 pre-commit 的 merge conflict、文件结尾、空白字符、Ruff、Ruff format 与 Prettier 检查。
