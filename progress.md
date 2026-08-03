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

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-08-02 | 根目录 `.env.sample` 不存在，环境模板读取失败 | 1 | 改为通过文件列表定位实际模板，不重复假定文件名。 |
| 2026-08-02 | Phase 8.1 首轮 Ruff 检查发现格式问题 | 1 | 统一格式并避免响应字段使用 Pydantic 保留的 `model_config` 名称后复查。 |
| 2026-08-02 | 字段重命名补丁命中错误文件，未写入 | 1 | 根据实际定义位置在研究 API 契约中修复，不重复使用原补丁。 |
| 2026-08-02 | 初次探查 `AsyncPostgresSaver` 时假定它具有 `__aenter__` | 1 | 确认 `from_conn_string()` 返回异步上下文管理器，按官方 `async with` 方式集成。 |
| 2026-08-02 | 前端端到端测试文件名假定错误 | 1 | 先列出实际测试目录再读取，不重复使用不存在路径。 |

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
| 2026-08-01 | 状态约束迁移按简写名删除失败 | 1 | 历史表使用最终约束名；改用 `op.f("ck_ingestion_runs_status")` 后成功应用 |
| 2026-08-01 | 本地 COMMENT 同步使用绑定参数失败 | 1 | PostgreSQL DDL 不支持该参数位置；改为固定注释文案后成功同步 |

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

| Question | Answer |
|----------|--------|
| Where am I? | Phase 1 至 Phase 5 已完成，下一阶段是端到端验证与前端接入准备 |
| Where am I going? | 以 Vue 原型为基准整理 OpenAPI 契约、恢复与隔离验证，再实现基于已完成向量索引的 RAG 检索与研究对话 |
| What's the goal? | 打通从研究要求到可研究文献集合的后端闭环，并为研究问答提供可信证据基础 |
| What have I learned? | Provider、准入服务和 RAG 入库 Worker 已存在；真实模型必须有明确 JSON 提示，SQLAlchemy 异步会话也必须显式管理只读后的事务边界 |
| What have I done? | 完成工作流、计划、检索、全文准入与集合构建 API，并以真实 PDF、Embedding 和 Milvus 验证入库完成后工作区可进入研究阶段 |

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
