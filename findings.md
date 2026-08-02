# Findings & Decisions

## Requirements

- 目标链路是“提交研究要求 -> 创建工作区草稿 -> 意图分析 -> 用户确认研究计划 -> 多源检索 -> 统一文献结果”。
- 前端原型已经按连续阶段设计，后端需要提供可恢复的服务端状态，而不是只返回一次性 JSON。
- MVP 不做付费功能；本阶段不实现 RAG 研究问答和复杂多 Agent。
- 工作流状态必须有清晰的中文代码注释、数据库列注释和接口展示说明；不能只给出英文机器值。

## Research Findings

### 2026-08-02 RAG 研究对话实现审计

- 已发现后端已有会话 API、受限混合检索、LangGraph、ARQ Worker、Redis Stream 与 PostgreSQL checkpoint；当前主要工作是把真实 API 接到独立对话页并进行真实验收。
- 前端当前已显式区分 `SearchRun*`（多源文献发现）和 `ResearchRun*`（集合内证据问答）；研究页必须继续保持这一边界，避免 UI 错用状态。
- 研究页应使用 `/research/:workspaceId` 独立布局和会话侧栏，不放入 `WorkspaceFrame` 的连续阶段侧栏。
- 本机默认 Node `v24.14.0` 与 pnpm `11.9.0` 不符合项目锁定的 Node 20 / pnpm 10 范围；后续检查使用项目指定运行时，不修改锁定策略。
- 2026-08-02 只读查询确认当前三个活动工作区都没有 `completed + is_current` 的入库版本和 L3 向量，不能直接作为真实 RAG 验收样本。真实验收需要创建并精确清理隔离的临时工作区，不能修改既有集合。
- 真实 RAG 验收已通过：临时工作区使用真实 Qwen embedding、Milvus、DeepSeek、LangGraph PostgreSQL checkpoint 与 Redis Stream；检索命中 2 个当前 L3 片段，回答保存 2 条可回链引用和 5 个阶段事件，随后按 UUID 清理。
- 验收过程中修复三项真实运行问题：Windows 的 psycopg checkpoint 必须在研究 Worker 创建循环前切换到 `WindowsSelectorEventLoopPolicy`；DeepSeek JSON mode 的提示词必须包含 `JSON`；当 `evidence_sufficient=false` 时，回答契约必须允许空引用并仅在证据充分时要求至少一条引用。
- 物理删除包含研究运行的工作区时，`research_runs` 与用户会话关系必须使用 `passive_deletes=True`，让已有 PostgreSQL `ON DELETE CASCADE` 生效，不能由 ORM 尝试把不可为空的外键更新为 NULL。

- `docs/03-literature-search-and-discovery-discussion.md` 要求先解析意图和确认方向，再生成多源检索表达式。
- `docs/10-frontend-interaction-flow-discussion.md` 明确提交时创建 `draft` 工作区，工作区阶段包括 `draft`、`analyzing`、`plan_review`、`retrieving`、`screening` 等。
- `docs/06-session-reliability-and-governance-discussion.md` 要求短期搜索状态由 Redis/arq 协调，不能依赖进程内存；长期 RAG 运行由 PostgreSQL 记录。
- 现有工作区 API 位于 `backend/app/api/routers/collections.py`，目前只覆盖工作区 CRUD，不接收研究计划和检索运行。
- 现有 Provider 位于 `backend/app/modules/search/providers/`，OpenAlex、Crossref、arXiv、Semantic Scholar 已有统一接口和配置。
- `process_provider_results()` 已实现规整、去重、初筛和 Provider 错误汇总，后续应由任务编排直接复用。
- `ResearchCollectionAdmissionService` 已负责 DOI、题录、全文和对象存储的严格准入；`app.workers.ingestion` 已负责 PDF 解析、分层切块、Embedding 和 Milvus 写入。
- `research_plans` 与轻量 `search_runs` 已建立，候选详情仍不建立 `search_candidates` 表；它们属于 Redis 短期会话状态。

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| 先设计工作流状态与 API 契约，再实现模型调用 | 防止模型输出直接驱动页面或绕过用户确认 |
| 搜索任务复用现有 Provider Registry 与处理链 | 已有来源、限速、错误语义和规整逻辑，减少重复实现 |
| 研究计划必须版本化 | 用户修改方向后不能静默覆盖已完成或正在运行的检索 |
| SSE 只推送可验证阶段和统计 | 前端需要进度反馈，但不能展示或猜测模型内部思维链 |
| 状态英文值配套中文元数据 | 保证数据库与 API 稳定，同时让代码维护者和前端使用者理解状态含义 |
| 工作区阶段和生命周期分离 | `workflow_stage` 只表达研究推进；`status` 保持 `active/archived` 生命周期职责 |
| 重复阶段事件幂等 | 浏览器重试和 arq 至少一次投递到达同一目标阶段时不重复写库 |
| 意图分析首版使用 OpenAI 兼容 JSON mode | 结构化输出经 Pydantic 二次校验；实际兼容网关必须在提示词中明确顶层字段和嵌套结构，不能假定它会自动遵守 schema |
| 多源检索使用独立 `search_run` 和 Redis 会话 | PostgreSQL 保存可恢复运行摘要，Redis 保存带 TTL 的候选和事件；确认计划与启动检索保持两个显式动作，避免用户确认时立即消耗外部来源配额 |
| Provider 编排采用确定性并发而非 Agent 自主循环 | 来源限速、单源失败隔离、规整去重和题录补全必须可预测；后续研究问答才使用 RAG Agent |
| 全文获取和长期准入拆分 | Redis 只保存搜索候选与全文下载短期状态；数据库只接受 DOI、正式题录和已校验 PDF 均具备的文献，防止未审核候选污染 RAG 数据 |
| `IngestionRun.pending` 是用户确认门槛 | 准入后先创建 `pending` 运行；仅“确认构建集合”会把它推进到 `queued` 并投递 Worker，`claim()` 只领取 `queued/failed` |
| 候选准入 API 不接受前端论文数据 | 前端只传递 `collection_id + search_run_id + candidate_id`；候选、题录、全文 URL 和暂存 PDF 结果均从用户拥有的 Redis 搜索会话读取 |
| 集合构建按单篇任务隔离失败 | 先以事务批量将 `pending` 切为 `queued`，再逐条投递 arq；队列不可用只令相应运行记录 `ingestion_queue_unavailable`，用户可创建新的重试运行 |
| 移出待确认文献采用归档而非物理删除 | 对象存储和 PostgreSQL 无法组成跨服务原子删除；因此 `CollectionPaper` 归档、最新运行取消，正式 PDF 留作审计并不再进入活动集合 |
| 入库读取与写入使用明确事务边界 | `AsyncSession` 的只读 `SELECT` 也会自动开始事务；在仓储层把 ORM 结果转成值对象后结束只读事务，后续状态/向量写入再显式开启独立事务 |

## Issues

| Issue | Resolution |
|-------|------------|
| 文档曾将“RAG 入库 Worker 未实现”与实际代码混用 | 以 RAG 讨论稿和 `backend/app/workers/ingestion.py` 为准，区分 Worker 已实现与自动投递 API 未实现 |
| 前端文档曾写确认后才创建工作区 | 已统一为提交要求时创建 `draft` 工作区，确认计划后才启动检索 |
| 工作流初版误建唯一索引 | ORM 的 `unique=True` 需要唯一约束；新增 `f41c8e7b2a06` 修正且保持数据不变 |
| 首次真实 JSON mode 输出使用自定义包装字段 | 提示词补充精确 JSON 形状后，真实模型已返回可通过 `ResearchPlanDraft` 校验的 3 个方向和对应查询计划 |
| 入库 Worker 在加载分块后报“transaction is already begun” | SQLAlchemy 的只读查询开启了隐式事务；读取时先提取值对象并结束该事务，写入 Embedding 状态时再开启短事务，已由仓储单元测试覆盖 |

## Resources

- `docs/03-literature-search-and-discovery-discussion.md`
- `docs/05-rag-research-workspace-discussion.md`
- `docs/06-session-reliability-and-governance-discussion.md`
- `docs/09-database-design-discussion.md`
- `docs/10-frontend-interaction-flow-discussion.md`
- `backend/app/modules/search/providers/registry.py`
- `backend/app/modules/search/processing.py`
- `backend/app/modules/collections/workspace_service.py`
- `backend/app/modules/collections/service.py`
- `backend/app/workers/ingestion.py`

## Visual/Browser Findings

- 当前静态原型首页以研究要求输入为唯一主操作，提交后在原位展示任务解析和后续阶段。
- 工作区切换器支持搜索和内部滚动，不通过独立工作区列表页中转。
- 静态原型的主流程状态为 `idle -> analyzing -> review -> searching -> ready`；计划确认和检索执行是连续画布内的子状态，而不是工作区中的平级页面。
- 原型中的产品阶段只有“任务解析、文献筛选、证据研究”。候选筛选需要展示处理明细、选中文献检查器，并在启动索引前给出明确的集合确认操作。
- Vue 已以 `ResearchRunnerView` 实现连续画布。`/workspace/:workspaceId/run` 只承载可恢复的工作区标识，不展示侧栏；工作区的筛选和集合页才进入带阶段栏的工作区壳。
- Playwright 工作流用例用 FastAPI 的实际 JSON 契约模拟认证、运行、候选和集合摘要，覆盖完成检索后的候选检查器与集合确认边界，不调用真实 Provider、模型或 Worker。

## Phase 6 Findings

- 工作区切换器接口采用 `q + cursor + limit` 游标分页；使用 `updated_at` 与 `id` 的复合排序键，避免同一更新时间下翻页重复或漏项。
- 搜索词匹配工作区名称和工作流阶段展示文本；阶段展示映射继续由后端统一提供，前端不复制中文状态字典。
- `GET /api/v1/collections` 返回 `{items, next_cursor}`，不再返回无上限数组；损坏游标映射为 422 和 `workspace_invalid_cursor`。
- 入口到检索启动的路由契约测试使用替身领域服务，验证 HTTP 顺序和恢复标识；真实数据库、队列与 Provider 行为继续由现有 live 集成测试承担。
- 真实 API 状态恢复测试证明：终态运行刷新时只需读取 PostgreSQL 摘要和 Redis 快照；运行中 SSE 可以从 `Last-Event-ID` 继续读取 Stream，前端不需要保存任务进度副本。
- 部分失败重试必须创建新的 `SearchRun` 尝试并保留旧运行；本地 API 验收使用队列记录器验证 `attempt_no=2`，没有意外触发外部 Provider。
- 外部 Embedding 和外部 Provider 测试均应使用显式环境开关；本地 PostgreSQL/Redis API 验收可以独立运行，避免网络失败掩盖权限与状态回归。
- 候选全文路由依赖检索运行的所有权校验，必须将底层 `SearchRunError` 映射为 HTTP 错误；否则跨账号的全文状态轮询会泄漏为 500。现已统一映射为 404、409 或 503。

## Phase 4 Verification

- 离线测试：98 个单元测试通过，Ruff、格式化、Pyright 和 `alembic check` 通过；完整测试集为 101 passed、4 skipped。
- 真实测试：`RUN_LIVE_SEARCH_RUN_TESTS=1 uv run pytest tests/integration/test_live_search_run.py -m live -s` 通过。
- 真实结果：OpenAlex、Crossref、arXiv、Semantic Scholar 均返回成功；75 条原始候选规整去重后得到 57 条候选，运行状态为 `completed`。
- 真实数据边界：候选和事件只写入 Redis 短期会话，`papers` 没有被搜索运行直接写入；临时用户、工作区、计划和运行已清理。
- Milvus 可见性：向量 upsert 后显式 flush，避免入库完成到首次检索之间因最终一致性出现空结果。

## Phase 5 Verification

- 真实端到端测试通过：以 arXiv 的 `Attention Is All You Need` PDF 完成“MinIO 暂存 -> 严格准入 -> 创建/确认入库运行 -> 解析与分层切块 -> 硅基流动 Embedding -> Milvus 写入 -> 工作区 `researching`”闭环。
- 硅基流动 `Qwen/Qwen3-Embedding-0.6B` 返回 1024 维向量；本次真实论文生成 L1=`4`、L2=`13`、L3=`47` 个 PostgreSQL 分块，并写入 47 条 Milvus L3 向量。
- `IngestionRun` 状态按 `pending -> queued -> running -> completed` 变化，完成运行被标记为当前版本；这证明“文献可研究”的门槛不只是 PDF 已下载，而是向量索引已可用。
- 测试使用专属 UUID、对象键与向量过滤条件清理临时数据，未删除既有用户数据、MinIO bucket、Milvus collection 或本地服务。

## Phase 7 Frontend Planning

- `frontend/` 已配置 Vue 3、Vue Router、Pinia、TanStack Query、Tailwind、Reka UI、Lucide、Vitest 和 Playwright，但 `src/` 目前只有目录占位文件，尚未有真实 Vue 页面或 API 客户端。
- 静态原型和 `docs/10-frontend-interaction-flow-discussion.md` 已定义入口、意图确认、检索结果、论文详情、研究集合和独立研究对话的页面契约；Vue 首版先实现前五个页面和集合构建，不等待 RAG 对话 API。
- 后端可直接接入的资源包括认证、工作区、研究计划、检索运行、候选、全文任务、集合文献和集合构建；候选详情与全文短期状态必须通过后端返回，前端不能自行拼装或持久化准入信息。
- TanStack Query 负责工作区、计划、检索运行、候选和集合状态等服务端数据；Pinia 只保存登录令牌引用、输入草稿、筛选展开和当前 UI 偏好等轻量客户端状态。
- SSE 事件只更新检索运行缓存和页面进度，页面刷新时必须重新读取 PostgreSQL/Redis 真状态，不能依赖本地动画进度。
- RAG 对话后端现已提供会话、消息、证据和回答 trace 接口；前端必须直接消费服务端回答与证据，不使用假回答冒充研究结果。

### Frontend Decisions

| Decision | Rationale |
|----------|-----------|
| 先实现“入口到可研究集合” | 后端契约完整且已有真实状态恢复测试，可以形成可运行的前后端垂直切片 |
| Vue 页面不直接编排 Worker | API 层负责创建任务，前端只观察状态、提交用户确认和重试动作 |
| 服务端状态优先 | 工作区阶段、计划版本、候选会话和入库状态必须以 API/SSE 为准，避免刷新后产生错误状态 |
| 对话入口先锁定 | 没有 RAG 检索与回答 API 时，不生成无法核验的演示答案 |
| 题录和全文按钮遵循准入状态 | 仅 `citation.status = ready` 的候选可渲染正式引用、允许请求全文；`conflict`、`none` 等状态必须显示服务端可理解的原因 |

## Phase 8 RAG 研究会话规划（2026-08-02）

- 前置闭环“研究要求 -> 可研究文献集合”已经完成真实验收，Phase 7.2 已验证前端在真实环境可恢复检索运行、展示候选并正确处理题录与全文准入状态。
- `conversations`、`messages`、`research_runs`、`research_evidences` 领域模型已经存在，且其中 `ResearchRun.langgraph_thread_id` 明确区分业务运行与 LangGraph checkpoint；Phase 8 应先审计迁移和实际 API 缺口，不应重复造表。
- 现有 Milvus collection 只保存 L3 向量和 `owner_user_id`、`collection_id`、`document_id`、`ingestion_run_id`、`level` 过滤字段，原文及父块仍在 PostgreSQL。因此检索必须先读取当前 `completed/is_current` 入库运行，再以用户、集合、运行和 L3 条件过滤 Milvus，最后由 PostgreSQL 二次校验并组装证据。
- 首个可交付模式固定为 `single_rag`：受限检索、一次查询改写预算、证据充分性判断和带定位引用回答。复杂跨论文问题随后才启用 Plan-and-Solve、受限 ReAct 和证据核验，不能将检索、权限或状态机交给无界 Agent 循环。
- RAG 回答的事实来源只能是本次运行写入 `research_evidences` 的片段。模型输出、查询改写、重排分数与 LangGraph checkpoint 都不能替代可审计证据。
- 真实验收需覆盖可回答、无证据、权限隔离、旧文档版本、Worker 重启和外部模型失败；仅在引用可定位和证据支撑基线可接受后扩展多 Agent 模式。

### Phase 7 Implementation Findings

- Vue 实现位于 `frontend/src/`：路由将认证、研究入口与工作区内计划、检索、结果、论文详情、集合构建分开；工作区壳负责显示服务端工作流阶段，而非前端自行推断。
- `src/api/` 只调用版本化 FastAPI 资源；Bearer Token 由 Pinia 认证状态维护，候选全文、集合构建和检索状态都不接受客户端伪造的文献元数据。
- 检索进度使用带 `Authorization` 头的 `fetch` 流读取 SSE，避免浏览器原生 `EventSource` 无法携带 Bearer Token 的限制；SSE 事件只更新 TanStack Query 缓存中的可展示状态。
- 工作区切换器使用后端的 `q + cursor + limit` 契约，以 TanStack Query 无限查询追加下一页，不解析不透明游标。
- 当前 Vite 页面覆盖登录和注册、研究入口、计划生成/确认、检索进度、候选结果、论文详情和研究集合；研究对话明确显示为后端 RAG API 未就绪，未使用模拟回答。
- `pnpm@11.9.0` 实际要求 Node `>=22.13`，不能和仓库固定的 Node `20.19.6` 共用。项目继续保持 `.node-version=20.19.6`、`packageManager=pnpm@10.34.5`；本次用 Node 20 直接执行本地已安装的 `vue-tsc`、ESLint 和 Vite 完成检查。
- 已修复两个前端恢复边界：结果页无 `run` 参数进入时会读取当前运行并同步其 ID；工作区切换器会按服务端阶段进入计划、检索、结果或集合页。
- `CitationFormat` 和 `format_citation()` 已有离线实现，但候选结果 API 只返回格式中立题录，尚无正式格式渲染端点。当前详情页的“标题 + DOI”复制不是正式引用，必须改为受题录 `ready` 状态约束的后端格式化 API。
- 候选正式引用端点现只接收工作区、运行、候选 ID 和格式枚举；它从服务端 Redis 会话取得 `ready` 题录，再调用既有 CSL/BibTeX 格式化器。`partial`、`conflict`、`unresolved` 题录返回明确冲突，不能被前端手拼为看似完整的引用。

### 真实前后端联调修复发现（2026-08-01）

- 真实工作区 `0066e4cf-a9cf-4087-a7f1-1d3beae013c2` 的工作流阶段为 `screening`，当前检索运行 `f454d6ec-3f65-40f7-bc42-b4a70f2cf7e4` 已完成，服务端统计为 49 条候选；前端重新登录进入 `/run` 时却落在计划确认页，说明恢复分支没有读取当前运行。
- 同一检索运行的候选结果页可正确显示 49 条，但检索运行画布显示 0 条；应以服务端当前运行的 `candidate_counts.candidate_count` 为终态数据源，不能以早期 SSE 事件或空快照覆盖。
- 候选题录目前存在 `conflict` 或 `none` 状态，尚无 `ready` 题录。TanStack Query 在 `enabled: false` 时仍可保持 `isPending`，因此详情页必须先判断题录状态，再显示“正在渲染”加载文案。
- 全文接口对未完成题录 DOI 核验的候选返回 `rejected` 和 `citation_not_ready`，这是正确准入行为。前端类型、轮询终止条件和按钮可见性都必须处理该终态，不能让页面无限显示“全文处理中”。
- 计划确认页的时间范围与语言边界已提取到 `features/research/scope.ts`；当前年份由调用方注入，以保证“不能大于今年”的规则可稳定测试。

- `GET` 候选全文状态在尚未请求全文时返回 404 是服务端的正确语义。前端不应为每个候选做后台探测；仅在用户已发起任务后轮询，才能避免把“尚无任务”误表现为浏览器错误。
- 真实浏览器回归已验证 `screening` 工作区自动恢复到结果页、49 条候选统计和 `conflict` 题录的入口限制。移除无全文状态探测后复跑通过，控制台错误数为 0。

### RAG 研究对话真实验收发现（2026-08-02）

- 旧 FastAPI 进程不会自动加载新增路由；重启 `uv run --directory backend uvicorn app.main:app --reload` 后，研究会话路由才出现在 OpenAPI 并可由 Vue 页面调用。
- 研究对话页独立于工作区阶段侧栏，真实浏览器已验证登录、集合范围展示、创建会话、侧栏折叠、删除确认和 SSE 研究接口契约。
- 原移动端 CSS 在 `max-width: 580px` 直接 `display: none` 隐藏会话侧栏，会让历史会话和新建入口永久不可访问；已改为可打开的抽屉并添加遮罩关闭。
- Alembic 的 `checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 和 `checkpoint_migrations` 属于 LangGraph 自有运行时表，不应被本项目 ORM 反射为待删除表；`include_object` 过滤后 `alembic check` 正常通过。
- 真实单轮 RAG 基线：Qwen/Qwen3-Embedding-0.6B 返回 1024 维，Milvus 召回 2 个 L3 片段，DeepSeek 回答保存 2 条可定位证据，Redis Stream 发布 5 个公开进度事件。
- 真实浏览器验收使用的账号、工作区、论文、文档和对象键均按精确 UUID 清理；删除会话保留服务端审计记录属于预期软删除语义。
- 真实多论文复杂比较、Worker 重启/取消及外部服务瞬时故障仍需独立基准样本，不能把离线 LangGraph 测试描述为完整线上验收。

### 真实功能链路验收补充（2026-08-02）

- 真实功能验收应区分“页面能显示”与“Worker 实际处理了任务”。本轮通过 FastAPI、Redis/arq、PostgreSQL、MinIO、Milvus、硅基流动 embedding 和 DeepSeek 实际跑通了入口检索链路与 RAG 问答链路，不依赖浏览器 mock 数据。
- 在 Windows 上，模块级 SQLAlchemy asyncpg 引擎会跨 pytest 用例复用连接。若每个异步测试创建独立事件循环，连接的 Future 可能绑定到已经关闭的事件循环；将 pytest 的默认测试 loop 固定为 session 级别可以让真实集成测试连续稳定执行。
- `EmailStr` 会拒绝 `example.invalid` 等保留顶级域名。真实注册 API 的测试邮箱必须使用正常语法域名，例如临时 `@gmail.com` 格式；这只是输入验证，不涉及外部邮箱投递。
- 外部文献源可用性需要按单源结果呈现。本轮一次来源验收中 OpenAlex（本地代理）连接失败、Semantic Scholar 兼容网关超时，而 Crossref 和 arXiv 正常返回；系统正确返回 `partial_failed`，并保留可用候选，不应把此类外部抖动误判为核心检索编排失败。
- 临时 API 研究集合在 RAG Worker 完成后，API 响应含两个当前文档版本的 evidence，回答正文显式使用 `【E1】`、`【E2】`；Redis Stream 有 5 条阶段事件，带 `Last-Event-ID` 的 SSE 连接能先获取数据库快照。

### 候选相关性评估 Agent 规划发现（2026-08-02）

- 现有候选理由位于 `frontend/src/features/research/candidate-reason.ts`，通过方向子议题与检索词的标题/摘要短语匹配生成 `core/related/background/boundary`。它是确定性展示辅助，不具备语义判断能力。
- 后端 `UnifiedCandidate` 已明确是 Redis 搜索会话中的短期对象；其 `triage` 只表示规则初筛（纳入、排除理由、警告），不应混入模型相关性结论。
- 已确认的设计边界：相关性 Agent 位于规则初筛之后、用户筛选之前；它仅基于原始问题、已确认方向/范围、实际检索词和候选公开元数据作判断，不能读取或杜撰全文内容。
- 最小可维护方案是在 `UnifiedCandidate` 增加结构化评估快照并通过现有搜索运行 Redis/SSE 机制传递；不为短期候选评价新增长期数据库表或绕过 DOI、题录、全文准入服务。
- 输出必须带标题或摘要中的可验证证据摘录。摘要不存在时只能输出“信息不足”或失败状态，不能由前端关键词匹配伪造理由。
- 当前 `SearchExecution` 在规整、初筛后先执行前 N 条题录补全，再完成运行；这会让与语义筛选无关的外部 DOI 请求占据结果可见前的关键路径。更合理的顺序是先发布候选快照、渐进完成语义评估，再按优先级或用户动作处理题录。
- 相关性 Agent 只接收统一候选的必要字段，而不接收全部原始来源记录或链接：标题、摘要、作者、年份、载体、类型、语言和基础警告足以解释相关性；来源追溯、DOI 和全文状态继续由确定性服务负责。

### 候选相关性评估 Agent 实施审计（2026-08-02）

- 现有未提交实现已经将 `relevance_state`、`relevance_assessment` 和 `relevance_error` 写入统一候选快照，并在检索 Worker 中按批次写回 Redis/SSE；候选可在 Agent 完成前展示。
- 审计发现还需补齐三项：评估对象需要显式的“主要研究内容”字段，前端不应拿整段摘要冒充 Agent 结论；模型配置或单项结构异常不能让整轮检索运行中断；失败候选需要有受所有权与会话 TTL 保护的单项重试入口。
- 旧的 `candidate-reason.ts` 与对应单元测试仍保留，虽然生产页面已不再调用它；继续保留会形成第二套候选理由事实来源，实施完成时必须删除。
## 2026-08-02 — Alembic 命名约定迁移陷阱

- `op.drop_constraint()` 会将传入名称经项目的 SQLAlchemy/Alembic 命名约定再次处理；传入已包含 `ck_search_runs_` 前缀的历史名会产生额外前缀。
- 在对本地数据库执行 `d4f8c2a9b715` 前，必须先查出真实约束名，并在迁移中以不会二次展开的方式删除该约束。

### Verification

- 已确认真实历史约束为 `ck_search_runs_ck_search_runs_stage`，迁移改为精确 DDL 删除后，`uv run alembic upgrade head`、`uv run alembic current` 和 `uv run alembic check` 均通过；本地数据库位于 `d4f8c2a9b715 (head)`。

### 候选相关性 Agent 真实验收与边界（2026-08-02）

- DeepSeek 的 JSON mode 对扁平对象比嵌套 Pydantic 模型稳定：它会返回 `candidate_id` 与评估字段同级的对象，而不是 `candidate_id + assessment` 包装。模型传输层应贴近实际输出，业务 API 仍保留稳定的嵌套 `CandidateRelevanceAssessment`。
- 兼容只接受无歧义形式：单个 `limitations` 字符串可视为单项数组，单个 `evidence` 对象可视为单项数组；缺字段、重复候选、未知候选或不属于标题/摘要的引文仍明确失败，不能被“兼容”吞掉。
- 模型不必提供 `source_field`。服务端可从同一候选的标题、摘要中确定其所属字段，再进行逐字证据验证；无法定位的引文会被拒绝。这样减少模型输出自由度，不降低证据边界。
- 相关性评估的输入预算由候选批量数、单篇摘要字符数、单批超时和最大输出 token 共同限制。批次按顺序执行，便于将每批完成结果立即写回 Redis 快照并避免并发写入竞争。
- `RUN_LIVE_CANDIDATE_RELEVANCE_TESTS=1` 只验证真实模型与证据回链，不写本地基础设施；`RUN_LIVE_SEARCH_RUN_TESTS=1` 则验证真实 Provider、Redis 快照和 PostgreSQL 运行记录。后者在单源超时下正确返回 `partial_failed`，而不丢失其他来源候选或其相关性结果。
- 前端相关性筛选只能消费服务端层级：优先审核=`core/related`，背景参考=`background`，需人工核对涵盖信息不足、失败、跳过与不建议优先。筛选不改变候选准入状态或用户已加入集合的选择。

### Phase 11 发布前审计发现（2026-08-02）

- 当前工作区包含此前尚未提交的完整垂直闭环，而非仅 Phase 10 的孤立改动：研究工作流、全文入库、RAG 研究会话、Vue 页面和候选相关性评估会作为同一个提交审计。
- 代码库没有 `.codegraph/` 索引目录，因此本轮按常规 Git 差异与定向检查审计，不调用 CodeGraph。
- 暂存区仅有相关性阶段迁移文件的格式化差异尚未暂存；该文件需要先以最终内容重新暂存，避免提交旧索引版本。
- 环境模板、开发说明和讨论稿已经记录模型调用预算、候选快照优先呈现、服务端单一理由来源和高相关候选的限额题录预取策略；提交前只需再次验证其与实现一致。
