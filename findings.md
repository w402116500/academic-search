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

| Decision                                       | Rationale                                                                                                                                |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| 先设计工作流状态与 API 契约，再实现模型调用    | 防止模型输出直接驱动页面或绕过用户确认                                                                                                   |
| 搜索任务复用现有 Provider Registry 与处理链    | 已有来源、限速、错误语义和规整逻辑，减少重复实现                                                                                         |
| 研究计划必须版本化                             | 用户修改方向后不能静默覆盖已完成或正在运行的检索                                                                                         |
| SSE 只推送可验证阶段和统计                     | 前端需要进度反馈，但不能展示或猜测模型内部思维链                                                                                         |
| 状态英文值配套中文元数据                       | 保证数据库与 API 稳定，同时让代码维护者和前端使用者理解状态含义                                                                          |
| 工作区阶段和生命周期分离                       | `workflow_stage` 只表达研究推进；`status` 保持 `active/archived` 生命周期职责                                                            |
| 重复阶段事件幂等                               | 浏览器重试和 arq 至少一次投递到达同一目标阶段时不重复写库                                                                                |
| 意图分析首版使用 OpenAI 兼容 JSON mode         | 结构化输出经 Pydantic 二次校验；实际兼容网关必须在提示词中明确顶层字段和嵌套结构，不能假定它会自动遵守 schema                            |
| 多源检索使用独立 `search_run` 和 Redis 会话    | PostgreSQL 保存可恢复运行摘要，Redis 保存带 TTL 的候选和事件；确认计划与启动检索保持两个显式动作，避免用户确认时立即消耗外部来源配额     |
| Provider 编排采用确定性并发而非 Agent 自主循环 | 来源限速、单源失败隔离、规整去重和题录补全必须可预测；后续研究问答才使用 RAG Agent                                                       |
| 全文获取和长期准入拆分                         | Redis 只保存搜索候选与全文下载短期状态；数据库只接受 DOI、正式题录和已校验 PDF 均具备的文献，防止未审核候选污染 RAG 数据                 |
| `IngestionRun.pending` 是用户确认门槛          | 准入后先创建 `pending` 运行；仅“确认构建集合”会把它推进到 `queued` 并投递 Worker，`claim()` 只领取 `queued/failed`                       |
| 候选准入 API 不接受前端论文数据                | 前端只传递 `collection_id + search_run_id + candidate_id`；候选、题录、全文 URL 和暂存 PDF 结果均从用户拥有的 Redis 搜索会话读取         |
| 集合构建按单篇任务隔离失败                     | 先以事务批量将 `pending` 切为 `queued`，再逐条投递 arq；队列不可用只令相应运行记录 `ingestion_queue_unavailable`，用户可创建新的重试运行 |
| 移出待确认文献采用归档而非物理删除             | 对象存储和 PostgreSQL 无法组成跨服务原子删除；因此 `CollectionPaper` 归档、最新运行取消，正式 PDF 留作审计并不再进入活动集合             |
| 入库读取与写入使用明确事务边界                 | `AsyncSession` 的只读 `SELECT` 也会自动开始事务；在仓储层把 ORM 结果转成值对象后结束只读事务，后续状态/向量写入再显式开启独立事务        |

## Issues

| Issue                                                    | Resolution                                                                                                                   |
| -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| 文档曾将“RAG 入库 Worker 未实现”与实际代码混用           | 以 RAG 讨论稿和 `backend/app/workers/ingestion.py` 为准，区分 Worker 已实现与自动投递 API 未实现                             |
| 前端文档曾写确认后才创建工作区                           | 已统一为提交要求时创建 `draft` 工作区，确认计划后才启动检索                                                                  |
| 工作流初版误建唯一索引                                   | ORM 的 `unique=True` 需要唯一约束；新增 `f41c8e7b2a06` 修正且保持数据不变                                                    |
| 首次真实 JSON mode 输出使用自定义包装字段                | 提示词补充精确 JSON 形状后，真实模型已返回可通过 `ResearchPlanDraft` 校验的 3 个方向和对应查询计划                           |
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

| Decision                   | Rationale                                                                                                            |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| 先实现“入口到可研究集合”   | 后端契约完整且已有真实状态恢复测试，可以形成可运行的前后端垂直切片                                                   |
| Vue 页面不直接编排 Worker  | API 层负责创建任务，前端只观察状态、提交用户确认和重试动作                                                           |
| 服务端状态优先             | 工作区阶段、计划版本、候选会话和入库状态必须以 API/SSE 为准，避免刷新后产生错误状态                                  |
| 对话入口先锁定             | 没有 RAG 检索与回答 API 时，不生成无法核验的演示答案                                                                 |
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

### Phase 12 候选审核与集合准备交互发现（2026-08-03）

- 当前 `ResultsView` 的 `selectedCandidateId` 只控制右侧检查器焦点，表格没有多选、跨页选择或候选准备清单；截图中的“确认 0 篇入集合”对应 PostgreSQL 中已有的待确认文献数量，而不是用户当前正在查看的候选数量。
- 当前单篇路径为：题录 `ready` 后请求全文，全文状态为 `available` 后才显示“加入待确认集合”。这个严格准入边界正确，但把准备动作藏在检查器中会迫使用户逐篇操作，无法支撑研究性筛选。
- 结果候选目前由 Redis 搜索快照一次性返回并在前端筛选，没有游标分页。研究审阅应使用稳定排序的传统分页，而不是无限滚动；准备选择必须按 `candidate_id` 保存到同一搜索会话，跨页、筛选和刷新不丢失。
- 交互术语必须区分：正在查看（前端焦点）、本次准备清单（Redis 短期选择）、待确认集合（PostgreSQL 中已满足准入的文献）与可研究集合（完成索引的 RAG 范围）。这四层不能复用“选择”一词。
- 批量操作只能编排现有的题录补全、全文下载和准入服务，逐项报告成功/失败，不能为了批量体验放宽 DOI、正式题录或全文门槛。
- 实现审计补充：`CandidateFulltextService.request` 已经把单篇候选投递到 arq 全文 Worker，Worker 会按需补齐题录、下载并验证 PDF；`ResearchCollectionAdmissionService.admit` 已封装对象转正、数据库事务和幂等处理。因此批量功能应只读取服务端准备清单后逐篇复用这两个服务，不复制全文或准入规则。
- 现有候选 Redis 主快照会被相关性重试更新；准备清单应放在同一搜索会话命名空间的独立 Redis 键中，避免勾选操作覆盖候选、相关性或计数快照。候选全文状态本来也是独立键，分页 API 可批量读取当前页状态并组装为审核视图。
- 真实 arXiv PDF 下载在当前网络下直连可用但吞吐较低；`127.0.0.1:7897` 代理也可用，且 20 秒内已传输约 1.86 MB（直连约 0.56 MB）。全文模块的 `FULLTEXT_NETWORK_MODE=proxy` 与 `LITERATURE_PROXY_URL` 能将代理使用限制在全文下载器，适合专项真实验收。
- `Document` 对 `IngestionRun` 的真实关系为一对多 `ingestion_runs`，没有 `latest_ingestion_run` 属性。任何验收应以 `IngestionRun.document_id` 查询运行记录，或由服务层定义明确的“最新运行”读取契约。
- P2 的阶段计时必须在写入 `completed`、`failed` 或 `cancelled` 终态之前关闭当前阶段；否则审计 trace 会把执行中的阶段错误标记为终态名称。`set_stage()` 已遵循正确顺序，三个终态路径需保持相同顺序。
- 当前单元测试仅有研究图、检索和计划服务覆盖，P2 需新增独立执行服务测试文件，避免把运行取消、配额和阶段计时仅留在端到端路径中。
- PowerShell 串联命令时，末尾成功的 pytest 会覆盖先前 Ruff 的非零退出码；静态检查和测试应拆分为独立命令，且在 `backend` 工作目录下只使用 `app/...`、`tests/...` 相对路径。
- `RawCandidate.language` 允许为空，但构造 `UnifiedCandidate` 时必须显式收窄为 `CandidateLanguage`；真实测试数据应以 `CandidateLanguage.UNKNOWN` 表达未知语言，不能把可空值传入稳定候选契约。
- P1 后候选相关性评估的成功单元测试必须显式注入独立主张核验替身；仅注入第一轮评估模型会让默认核验器尝试创建真实聊天客户端，并按安全策略把候选降级为可重试失败。
- 本地 PostgreSQL 已成功应用 `e5c7a9d1b208`（research run governance state）并处于 `head`；`alembic check` 没有生成待迁移操作，数据库模型和迁移当前一致。
- P2 真实治理专项在 2026-08-03 通过（`1 passed in 5.11s`）：使用真实 PostgreSQL、Redis 与当前 Worker，在阻塞路由模型返回后确认协作取消；运行终态为 `cancelled`、Redis 有终态事件、未写回答或证据，计时 trace 关闭的是 `preparing`，用户及全局每日额度均稳定拒绝。测试随机创建并清理用户、集合、论文、文档、入库运行与 Redis stream。
- 真实 OpenAI 兼容模型已观察到以 `router` 和 `choice` 字段表达与路由 schema 等价的 `mode`。输入规范化只在 `mode` 缺失且恰有一个已知别名时映射，随后仍由 `Literal["single_rag", "multi_agent"]` 校验值；多个或未知别名不接受，避免把不确定模型输出伪装成有效路由。
- 2026-08-03 的第三次真实模型 RAG 专项在路由兼容层生效后超过 124 秒外层执行时限，未得到可报告的成功结果。随后按精确测试标识审计 PostgreSQL，未发现遗留集合、论文或运行，说明测试的 `finally` 已执行；不再重复同一长链路。P2 的真实治理专项不依赖该外部模型调用，已另行通过。
- P2 讨论稿的配额/预算范围明确包括检索、集合构建入库、深度生成和问答；当前 `ResearchConversationService` 的每日限制只覆盖研究问答。为保持与讨论稿一致，P2 仍需将同类可解释的用户/全局日限制接入搜索运行和集合构建投递边界，不能仅凭问答治理验收结束。
- 检索 Worker 已在 Redis/SSE 的 `candidate_counts` 中发布 `relevance_total_count`、`relevance_completed_count` 与 `relevance_failed_count`；等待页目前没有将这些真实统计呈现为阶段进度，且阶段轨迹漏掉了 `relevance_assessment`，导致用户看到“处理中”却看不到实际推进。
- 续作审计确认：候选审核后端、分页 API 和结果页主结构已经落地，但仍需收紧两个边界：未通过基础初筛的候选不得进入 Redis 准备清单；非法 Base64 游标必须转换为稳定的 4xx 业务错误，不能冒泡为 500。
- 单篇全文按钮与批量“准备核验”必须共享同一准入含义。全文 Worker 可在任务内补齐题录，因此前端单篇入口不应额外要求 `citation.status = ready`，只应要求 DOI 存在且尚无全文终态。

### Phase 13 检索等待页可验证进度体验（2026-08-03）

- `SearchExecution` 已发布的相关性总数、完成数、失败数和 `message` 是等待页唯一的进度事实来源；前端不根据耗时、批次数或模型行为推算百分比和剩余时间。
- 检索阶段顺序应与 Worker 一致：多源检索、记录规整、去重与初筛、相关性判断、题录补全。运行中展示最近一次 SSE 说明与更新时间，连续 15 秒无事件时只重连进度流，不重新创建搜索任务。
- 前端验证通过：Node `20.19.6`、pnpm `10.34.5` 下 Prettier、ESLint、vue-tsc、Vitest（18 passed）、Playwright（6 passed）与 Vite production build 全部成功；本地浏览器 smoke 确认认证入口可用，未读取用户会话数据。

### Phase 14 核验任务页面交互发现（2026-08-03）

- 用户截图显示候选列表行仅呈现“全文处理中”，用户无法知道本次准备清单是否已提交、每篇在题录补全还是全文校验、何时可进入待确认集合。
- 交互边界应固定为：候选列表负责建立准备清单；核验任务页负责显示可验证的批量任务状态；待确认集合只接收题录与全文均已通过的候选；集合构建仍是后续独立确认动作。
- 不应等待整批全部完成才允许入集合。已准备好的候选应在核验页进入“可加入”范围，用户确认后写入待确认集合，未完成或受阻项继续保留在同一任务页。
- 现有 `getSearchCandidates(..., { filter: "selected" })` 已返回准备清单的服务端分页、每篇 `fulltext` 状态与汇总计数；`FulltextStatus` 已覆盖 `queued`、`downloading`、`validating`、`available`、`failed`、`rejected`，可直接驱动真实任务说明。
- 现有批量准入接口只接收当前已具备可处理全文的候选，且后端会把成功项从 Redis 准备清单移除，未完成或受阻项保留。因此核验任务页不需要新的准入逻辑，只需在本页展示和触发现有接口。
- 本轮页面属于已有工作台的定向演进，不改变路由信息架构或业务术语。设计采用浅色研究工作台、深青单一强调色、低视觉密度和受限的状态过渡；动效只用于“正在更新/任务状态变化”的反馈，并为 `prefers-reduced-motion` 提供静态降级。
- 集合准入的唯一交接页应是核验任务页。候选结果页的可用状态和论文详情页的单篇核验都可以引导进入该页，但不应再各自调用准入接口；详情页先把单篇候选加入准备清单，确保它不会在批量任务视图中丢失。
- 页面验证以公开的 `FulltextStatus` 为事实来源：`queued`、`downloading`、`validating`、`available`、`failed` 和 `rejected` 分别映射为等待、处理中、已通过或受阻说明。页面不显示不可验证的进度百分比、剩余时间或题录内部阶段。

### 实施对齐基线与整改范围（2026-08-03）

- `docs/11-implementation-alignment-discussion.md` 是本轮事实基线：代码当前的最大偏差是候选相关性评估按小批串行调用，批次不共享语义上下文；候选终态列表也按年份/标题而非相关性排序。
- P0 只修复集合级相关性判断、相关性优先稳定分页和过期文档事实。候选仍是 Redis TTL 短期状态；标题/摘要仍是候选理由的唯一证据；题录、全文和入库继续逐篇独立处理。
- P1/P2 已写入计划但不得提前标记完成：上传、真实重排、单轮答案二次核验、复杂路由、协作取消与配额均须先由代码和验收证明。
- 候选审核 API 在检索运行中也可读取快照，因此排序分为两个版本：运行中保留年份/标题的发现顺序以避免跳页；`completed` 或 `partial_failed` 后切换到相关性层级优先。游标指纹必须包含排序版本，否则一个运行中游标会错误地穿透到终态排序。
- 现有单项相关性重试仍只在检索终态、失败且 `retryable=true` 时调用。集合超过容量上限不是单篇重试能解决的问题，必须标为不可重试并引导用户缩小检索；无摘要候选继续由服务端确定性完成为“信息不足”。
- P0 验收完成：后端全量 `150 passed, 11 skipped`，Ruff、格式、Pyright 与 Alembic check 通过；前端 Node `20.19.6` / pnpm `10.34.5` 的 typecheck、ESLint、Prettier、Vitest（19 passed）、Playwright（6 passed）和生产构建全部通过。
- P0 真实验收完成：真实模型的多候选集合测试在同一次调用中处理 2 条候选并通过全部元数据证据回链（`1 passed in 9.09s`）；PostgreSQL、Redis、MinIO、真实 arXiv PDF 下载、校验和批量准入专项通过（`1 passed in 18.56s`）。后者的 `finally` 清理临时用户、Redis 键、对象和数据库资源；健康检查确认 PostgreSQL 与 Redis 可用，Compose 中 Milvus、MinIO、PostgreSQL、Redis 均健康。

### P0 当前环境真实复验（2026-08-03）

- 本轮用户要求先真实测试。复验将重新运行多候选集合级模型调用和真实 arXiv 到候选准入专项，不将先前验收输出作为本轮结论。
- 本仓库根目录没有 Compose 配置，`docker compose ps` 因此不能作为健康检查。Docker 当前健康实例为 PostgreSQL（主机端口 `55432`）、Redis（`6379`）、MinIO（`9000`）和 Milvus（`19530`）；后续测试使用项目环境配置，不假设 PostgreSQL 位于默认 `5432`。
- 本轮真实模型复验通过：集合级候选相关性专项在一次模型调用中处理 2 条候选，且逐条标题/摘要证据回链均通过（`1 passed in 7.65s`）。
- 本轮真实候选准入复验通过：arXiv PDF 下载/校验、Redis 准备清单、MinIO 暂存/转正与 PostgreSQL 准入专项均成功（`1 passed in 18.08s`）。测试后只读查询确认临时 `example.invalid` 用户无残留；P1/P2 仍是规划项，不能由本次 P0 复验推定为完成。

### P1 实施基线（2026-08-03）

- `docs/11-implementation-alignment-discussion.md` 将 P1 严格限定为四项可验证能力：候选理由主张二次核验、当前用户与当前候选且有明确授权声明的 PDF 上传暂存、真实且可替换的 Reranker、单轮回答后独立的原子主张—引用片段核验。
- 已确认不能改动的边界：候选理由只能使用标题/摘要；上传必须复用现有 PDF 校验、对象转正和严格准入；RAG 仍只能读取当前用户、当前集合、当前文档版本内的证据，Milvus 之后仍需 PostgreSQL 二次权限校验。
- 实施顺序固定为先补契约和服务边界，再补 API/前端与真实验收；不得把 RRF 截断表述为重排，也不能把模型输出或任务计划当作完成事实。
- `candidate_relevance.py` 目前仅验证每条 `evidence.quote` 在同一候选标题或摘要中的逐字存在性；`study_focus`、`reason`、`helpful_aspect`、`limitations` 和 `recommendation` 尚未做主张蕴含核验。因此 P1 需要在这一步之后增加独立、可替换的核验边界，失败时不继续展示未经核验的结论。
- `research/retrieval.py` 目前在 RRF、父块合并和最低 RRF 阈值后直接用 `rag_final_evidence_limit` 截断。`RetrievedEvidence` 与数据库模型已有 `rerank_score` 字段，但当前永远不计算、不写入。
- `research/graph.py` 已有回答前 `verify_evidence(question, evidences)`，它只挑选可回答问题的片段；`generate_answer()` 后没有原子主张—引用片段的独立核验。`research/execution.py` 也固定把所有证据标为 `selection_stage="rrf"` 且 `rerank_score=None`，须随真实能力同步。
- `CandidateFulltextService` 已对 `owner_user_id + collection_id + search_run_id + candidate_id` 执行所有权、终态搜索运行和 Redis 候选会话校验；`CandidateReviewService.admit_selected()` 只复用同一短期全文状态中的 `available` 结果调用严格准入服务。上传应写入这个既有状态，不能另建绕开审核的“上传即入库”路径。
- `OpenAccessPdfAcquirer` 已有流式体积限制、`application/pdf` MIME、PDF 签名、SHA-256 和私有对象暂存。它的候选校验额外要求开放获取 URL；P1 上传需要抽取可复用的 DOI/题录一致性校验，同时不允许客户端传 URL、DOI 或对象键。
- 候选审核路由目前提供分页、准备清单、批量准备、批量准入和相关性重试；候选详情页只能调用“准备全文核验”。前端对 `requires_upload` 尚未提供选择 PDF、明确授权或把上传状态写回同一核验任务的操作。P1 上传 API 应保持在现有 `collection + search_run + candidate` 路径下，服务端从 Redis 会话取得候选，而不是接受客户端题录数据。
- P1-A 候选理由核验已接入 `OpenAICompatibleCandidateRelevanceEvaluator`：第一轮通过逐字引文校验后，第二个独立结构化核验器必须确认 `level`、研究内容、理由、帮助、限制和建议均未超出标题/摘要与研究上下文。任一候选缺失、重复、格式错误、模型不可用或字段不受支持时，该候选理由被拒绝且保留可重试失败码；离线专项现为 `6 passed`。
### P1 真实验收待执行基线（2026-08-03）

- 当前计划的 P1-A 至 P1-C 均仍为未完成项。已有记录称候选理由二次核验的离线专项为 `6 passed`，但这不能证明真实模型、上传授权、Reranker 或 RAG 回答二次核验已经通过。
- 本轮真实验证必须从代码中的 live 测试开关、实际配置和服务健康状态重新确定命令，并将每条结果写回计划记录。
- 运行环境中 PostgreSQL、Redis、MinIO 与 Milvus 的 Docker 容器均健康。真实模型的 DeepSeek 配置存在；真实 Reranker 的 URL、密钥和模型均未配置，因此不能运行或宣称 P1-C 的真实 HTTP Reranker 验收。
- `test_live_candidate_relevance.py` 会在同一 `assess()` 调用中处理两条候选，且不写入本地基础设施。随着当前 P1-A 实现，评估器会在逐字证据校验后调用独立主张核验器；这条专项将验证该真实模型链路。
- 真实 P1-A 验收第一次执行失败（Python 3.12.13，`1 failed in 57.35s`）：至少一条候选在独立主张核验后为 `failed`。此结果不能通过移除二次核验来规避；应先诊断核验失败码和对应的第一轮理由字段，再决定是收紧生成提示、调整真实验收数据，还是修正服务端对模型协议的处理。
- 真实诊断证明二次核验拒绝是正确的：第一轮理由把摘要中的关联扩大为“保护作用”和“机制”，又加入“发表偏倚”“残余混杂”“外推性”等摘要外限制。P1-A 修复应约束第一轮仅生成元数据可直接表述的理由；独立核验仍须拒绝任何候选特定的扩大结论。
- 讨论稿的验收目标是“伪造或扩大解释的理由被拒绝或降级，前端不作为确定结论显示”，不要求真实模型每次都完成所有候选。因此真实验收应先断言安全不变量：完成态必须是独立核验通过的理由；拒绝态必须没有 `relevance_assessment` 且有可重试的明确失败码。正向完整通过仍需由受控、合规的真实模型输出复验。
- 收紧第一轮生成契约后，真实 P1-A 候选理由专项在 2026-08-03 通过（Python 3.12.13，`1 passed in 17.62s`）：同一次集合级模型调用的两条候选均通过独立主张核验后才完成（`2 completed, 0 rejected`）。该结果仅证明候选理由子项；不推定 P1 的上传、Reranker 或 RAG 回答核验已完成。
- 真实 RAG Worker 专项在 2026-08-03 通过（Python 3.12.13，`1 passed in 161.78s`）：真实 embedding 写入 Milvus、受限检索返回两条引用片段，回答主张核验 trace 为 4 条主张、0 条不支持。运行记录为 `completed`，Redis 产生 6 个事件，`finally` 输出确认本次 PostgreSQL、Milvus、Redis 与 checkpoint 资源已删除。真实用例此前只打印该 trace，现已补为断言。
- P1-B 的首次真实上传验收暴露了服务缺陷：`CandidateReviewService.admit_selected()` 的“每项独立 rollback”会使早先读取的 `SearchRun` ORM 实例过期，方法末尾再读取 `run.id` 会在异步上下文触发 `MissingGreenlet`。应在首次读取运行后保存 `run_id` 标量；不能删掉 rollback，因为它负责隔离逐篇准入失败。
- P1-B 的第二次真实上传验收确认了权限值不一致：`AuthorizedPdfUploader` 曾产生 `access_rights="user_authorized"`，但数据库 check constraint、`ResearchCollectionAdmissionService` 白名单和数据库讨论稿都约定 `user_upload`。已统一为 `user_upload`，不新增迁移；“用户明确授权”是请求授权声明和服务端会话所有权的流程事实，不应伪造成未获 schema 支持的新权限枚举。
- P1-B 真实 MinIO/Redis/PostgreSQL 专项在 2026-08-03 通过（Python 3.12.13，`1 passed in 5.17s`）：上传只从服务端 Redis 候选读取身份与 DOI，授权 PDF 的 MIME、签名、大小、SHA-256 与 `user_upload` 权限记录均通过，再经现有严格准入创建正式文档和 pending 入库运行。测试清空本次准备清单并删除临时对象、Redis 键和数据库记录。
- P1-C 的未配置分支已在真实 RAG Worker 中验证：trace 为 `reranker.enabled=false`、`status=disabled`，只按 RRF 截断且 `rerank_score` 不产生；研究对话页基于同一 trace 显示“未启用模型重排”。HTTP 适配器的请求、响应下标和实际分数写回由 4 条离线契约测试覆盖。当前环境没有 Reranker URL、密钥和模型，不能将真实服务验收标为通过。

### P1 前端契约复核（2026-08-03）

- 后端 `FulltextResponse` 已返回 `requires_upload`，但前端 `FulltextStatus` 联合类型遗漏该值，使候选详情和核验任务页面无法通过类型检查，并会让详情页把等待用户上传误当作需要继续轮询的异步任务。
- 修复范围限定为前端 API 契约、终态判断、公共状态文案与结果页的状态说明；授权上传接口、PDF 准入规则和候选会话边界不作变更。相应单测将覆盖该状态的终态及核验任务呈现。
- 初次全套浏览器回归的 4 个失败共享同一根因：`PaperDetailView.vue` 在“前往核验任务加入集合”按钮后遗留裸 `>`，使 `requiresUpload` 的 `v-else-if` 不再与前一分支相邻。移除该字符后，上传、候选审核、桌面与窄屏工作流全部通过。
- P1 前端最终验收通过：Prettier、vue-tsc、ESLint、Vitest（19 passed）、Playwright（7 passed）、Vite production build 和 `git diff --check` 均无错误。当前 `.env` 中只读确认 `RAG_RERANKER_URL`、`RAG_RERANKER_API_KEY`、`RAG_RERANKER_MODEL` 都未配置；P1-C 只能保留为真实服务验收待配置。

### P2 治理范围续办（2026-08-03）

- `ResearchConversationService._assert_submission_quota()` 已为研究问答实现 UTC 自然日用户/全局配额；`SearchRunService.start_search()/retry_search()` 与 `CollectionBuildService.build()/retry_run()` 尚无等价检查。
- 后续实现会在四个创建新运行的边界计数实际已提交记录；批量集合构建将按本次真正待投递文献数预检，避免只限制请求数而让单次批量绕过文档运行预算。
- 已新增 `ingestion_runs.submitted_at` 及索引，由 `build()` 在 `pending -> queued` 的同一提交内写入、由 `retry_run()` 在新运行创建时写入；配额只统计该时间，避免“已准入但未确认构建”的文献提前消耗额度，也避免跨日确认构建按记录创建日误计。
- 搜索的 `start_search()`、`retry_search()` 与入库的 `build()`、`retry_run()` 均已在创建/推进运行前执行 UTC 自然日用户与全局计数检查，分别使用稳定错误码和 HTTP 429。批量构建用 `已用数 + 本批 pending 数 > 限额` 预检，拒绝时不改变任何 pending 状态。
- 真实 PostgreSQL 治理专项已显式执行：`RUN_LIVE_RESEARCH_GOVERNANCE_TESTS=1 uv run --python 3.12 pytest tests/integration/test_live_research_governance_e2e.py -m live -s -q` 返回 `2 passed in 5.47s`。新增用例不调用外部模型或 Redis 队列，确认搜索用户/全局、入库批量用户/全局额度均在队列调用前拒绝，并在读取后确认两条 pending 运行仍无 `submitted_at`。
- P2 全量验收完成：后端 `ruff format --check .`、`ruff check .`、Pyright（0 errors）和 pytest（`176 passed, 14 skipped`）均通过；Alembic 已升级到唯一 head `a9f3c7d2e6b4` 且 `alembic check` 无待生成操作。前端使用 Node 20.19.6 / pnpm 10.34.5，Prettier、typecheck、ESLint、Vitest（19 passed）、Playwright（8 passed）和生产构建均通过。
- `05`、`06`、`11` 号讨论稿现已明确 P2 完成、搜索/入库/研究问答的额度边界和真实验收，同时保留两项不可夸大的限制：真实 HTTP Reranker 未配置；真实模型长链 RAG 专项最后一次超过外层时限，不作为本阶段通过依据。
- 最终只读配置核对确认 `RAG_RERANKER_URL`、`RAG_RERANKER_API_KEY` 与 `RAG_RERANKER_MODEL` 均为 absent；因此 P1-C 仍是唯一等待外部环境的真实服务验收项。`git diff --check` 通过，只有 Git 的 CRLF 预告警，没有空白错误。
- 已在实际 `.env` 的模型服务配置后加入三项空 Reranker 配置，并在 `ResearchSettings` 将空 URL、Key、模型归一为 `None`；三项同时留空时不会触发“半配置”错误，读取设置确认 `reranker_enabled=False`。推荐填写模型为 `Qwen/Qwen3-Reranker-0.6B`，但必须与真实 URL、Key 同时设置。

### P1-C 真实服务验收启动（2026-08-03）

- 用户已完成实际 `.env` 的 Reranker 配置；安全存在性检查确认 `RAG_RERANKER_URL`、`RAG_RERANKER_API_KEY` 和 `RAG_RERANKER_MODEL` 均为非空，不读取或记录配置值和密钥。
- 真实验收将直接覆盖 `HttpResearchReranker` 的外部 HTTP 协议、结果下标、分数与中文样本排序。它不同于此前已超时的完整 RAG Worker 长链路，不写入 PostgreSQL、Milvus、MinIO 或 Redis。
- 首次真实调用在配置的 SiliconFlow `/v1` 服务根路径收到 HTTP 404；继续检查发现 API Key 与模型变量互相填反。已在本地 `.env` 改为完整 `/v1/rerank` 路径并交换两项值；不修改 RAG 检索算法，也不读取或记录密钥。
- 配置纠正后，`RUN_LIVE_RERANKER_TESTS=1 uv run --python 3.12 pytest tests/integration/test_live_reranker.py -m live -s -q` 在 2026-08-03 通过（`1 passed in 2.14s`）。真实服务以 `Qwen/Qwen3-Reranker-4B` 对三段中文候选返回完整且不重复的输入下标、有限分数和降序结果；直接回答 multi-head attention 作用的候选排在首位。
- Research Worker 的 `startup()` 只在 `reranker_enabled` 时创建并注入 `HttpResearchReranker`；本地常驻 Research Worker 已重启，使进程缓存读取本次 `.env` 配置。未发现需要重启的常驻 Uvicorn API 进程。
- P1-C 收尾回归通过：`ruff format --check .`（172 files）、`ruff check .`、Pyright（0 errors）和 pytest（`177 passed, 15 skipped in 12.29s`）。新增真实专项默认跳过，只有显式设置 `RUN_LIVE_RERANKER_TESTS=1` 才会调用外部模型。
- 完整 RAG + Reranker 真实链路在 2026-08-03 通过：`RUN_LIVE_RESEARCH_E2E_TESTS=1 uv run --python 3.12 pytest tests/integration/test_live_research_e2e.py -m live -s -q` 返回 `1 passed in 165.34s`。临时集合实际完成 Qwen embedding、Milvus 受限召回、`http_reranker`（2 个候选、2 个返回）、DeepSeek 单轮回答、4 条主张的独立证据核验（0 条不支持）及 Redis 7 个事件；`finally` 输出确认 PostgreSQL、Milvus、Redis 与 checkpoint 资源已删除。

### P2 实施基线（2026-08-03）

- `ResearchGraphRunner` 目前以 `_COMPLEX_MARKERS` 的关键词命中直接决定模式；`_run_complex()` 仅执行一次子问题规划、一次受限并行检索、一次证据核验和一次回答生成。它有工具数量上限，却没有基于工具观察决定继续、停止、澄清或拒答的受限循环，也没有路由依据 trace。
- `ResearchConversationService.cancel_run()` 仅允许 `queued` 运行取消。Worker 的阶段回调虽会拒绝覆盖终态，但图、外部模型/检索调用与最终 terminal SSE 发布没有共同的运行中取消协作边界。
- `ResearchRun` 当前可存储 `model_config` 和 `retrieval_trace`，但没有用户级配额计数、全局成本预算或阶段耗时/失败指标的持久字段；`ResearchSettings` 也尚未定义对应阈值。P2 需以明确服务契约和审计 trace 实现这些能力，而不是在前端估算。

### 意图分析失败页排查（2026-08-04）

- 用户截图显示工作区“我想研究睡眠质量与心理健康之间的关系”停留在计划确认页，持久化提示为“研究意图分析模型暂时不可用，未生成检索计划”。这发生在检索/RAG 前的意图分析阶段，与已真实验证的 Embedding、Reranker 和 RAG 回答链路无直接因果关系。
- 页面当前操作为“重新读取”；此前初步定位表明它可能仅调用查询 `refetch()`，会重新读取同一个失败记录而不触发计划重生成。待沿 UI、API、Worker 和模型适配器链路实测确认。

### 真实浏览器全链路验收（2026-08-04）

- 用户指定以“我想研究睡眠质量与心理健康之间的关系”作为真实前端验收要求。验收将使用临时账号，不操作用户现有工作区；目标覆盖研究入口、计划、检索、候选审核、集合构建以及有可准入全文时的研究对话。
- 真实浏览器已到达注册页并填入临时账号；点击“创建账号”后前端展示“输入不符合格式要求，请检查后重试”，网络请求为 `POST /api/v1/auth/register` 的 HTTP 422。尚未重复提交，待读取实际注册契约后改用合规测试数据。
- HTTP 422 响应明确指出 `example.test` 是保留域名；后端 `EmailStr` 的拒绝符合契约，非注册实现故障。浏览器的 `type=email` 未阻止该格式，因此当前 UI 只能显示通用格式错误；真实验收将改用唯一的普通邮箱格式继续。
- 使用普通格式临时邮箱后注册成功（HTTP 201），前端真实创建了工作区 `cb21915b-36fe-4de2-838b-6fd96db119c3` 和计划 `9df3c55a-f808-4df5-91c6-03ac856ab11c`（HTTP 201）。前端状态正确显示 `RUNNING` 且轮询计划接口；最新数据库/API 响应仍为 `generating`，无错误代码或模型快照，说明任务尚未被成功完成或失败落库，需检查 Workflow 队列消费。
- 随后以计划 UUID 核对 PostgreSQL 与 Redis：计划已在 `2026-08-04T03:17:12Z` 进入 `ready`，有模型快照且无错误；`arq:result:<plan-id>` 存在、任务与 in-progress 键均已清理，工作流队列为空。此前浏览器看到的 `generating` 是 Worker 正常处理窗口，而不是积压或卡死；可继续在真实页面确认计划。
- 在重新加载工作区路由后，浏览器的临时认证状态已失效并被鉴权守卫重定向到 `/login?redirect=...`；登录页正确保留目标工作区路径。已用隔离测试账号重新填写邮箱和密码，准备提交并验证可恢复至同一计划。
- 重新登录后实际回到同一工作区并显示 `READY` 计划。模型生成 3 条方向；默认选中的“睡眠障碍与抑郁/焦虑的关联及双向机制”直接覆盖原始研究要求，另外两条分别聚焦睡眠质量对情绪调节/心理韧性和睡眠干预的心理健康效果。验收将保留默认主线继续，不改写模型意图。
- 真实计划确认页的检索范围为 2018–2025 年、中文和英文；右侧摘要列出 OpenAlex、Crossref、Semantic Scholar 等计划来源，并明确 DOI、正式题录与可处理全文仍须后续准入。范围未被本次验收修改。
- 通过真实页面确认计划后创建检索运行 `d466bb54-6ae2-44a5-879a-5fa3e8c721cd`。页面显示 OpenAlex 和 Crossref 各返回 25 条、规整后 50 篇可审核候选；Semantic Scholar 请求超时，运行状态为“部分完成”，其余来源的结果仍可继续审核。这验证单源失败隔离与可恢复呈现，不将部分失败伪装为全量成功。
- 前端已从检索进度进入同一运行的候选审核页；阶段栏显示“50 条收集、50 条合并后保留”，来源卡片分别为 `openalex completed`、`crossref completed`、`semantic_scholar failed`，准备清单为 0。当前首个题录缺少摘要，检查器显示“信息不足”而未伪造相关性判断；将通过“开放获取”筛选寻找可走真实全文准入的候选。
- 点击真实“开放获取”筛选后列表缩至 25 篇。可见 `The European Insomnia Guideline: An update on the diagnosis and treatment of insomnia 2023`，有 DOI 且题名与睡眠主线相关；尚未加入准备清单，先打开该候选的服务端检查器核对相关性与全文状态。
- 已通过候选列表实际打开该失眠指南。检查器显示完整题名、作者与期刊信息；列表的服务端状态仍为“分析未完成”，原因是本轮 50 条初筛候选超过单次相关性判断上限 24 条。前端没有用题名或筛选标签伪造评估结论；接下来查看全文获取/准入操作。
- 检查器底部显示该候选可“加入本次准备清单”或“准备单篇核验”，并说明准备清单只存在于当前检索会话、未经 DOI/题录/全文准入不会进入长期文献库。将选择“准备单篇核验”而不是绕过全文验证直接构建集合。
- 已在真实页面触发失眠指南的“准备单篇核验”。页面 toast 显示“单篇题录与全文核验已安排”，操作区不再提供重复启动入口而转为“查看完整记录”；这证明操作已异步提交，待核对最终获取/准入结果。
- 完整核验页已显示题名、作者、期刊、年份和 DOI `10.1111/jsr.14035`。Playwright 控制台有两条同路径 HTTP 409：候选引用格式接口 `.../citation?format=gb_t_7714_2015_numeric`，属于页面自动请求引用补全而非全文下载请求；应作为前端真实回归发现单独定位，不能据此判定全文核验失败。
- 详情页显示完整摘要，并明确“尚未进入研究集合：有 DOI 不等于系统已准备有可研究论文”。正式引用区显示“正式引用预览暂时无法读取，请稍后重试”，与前述 citation API 409 一致。该页没有把 DOI、摘要或开放获取标签误呈现为入库成功；返回候选页确认异步全文核验终态。
- 返回候选页后，“全文已核验”筛选返回 0 条，说明本轮没有任何候选被系统标为已验证全文；不能继续伪造集合构建或 RAG 对话。下一步读取同一搜索运行 Redis 快照中失眠指南的完整全文状态与错误码，并再由页面核对可恢复路径。
- 搜索运行持久状态为 `partial_failed/completed`；其 Redis 候选快照确认失眠指南有 DOI `10.1111/jsr.14035`、OpenAlex 提供的 Wiley PDF 直链和 `is_open_access=true`，但 `citation=null`，相关性状态为 `failed/candidate_relevance_collection_too_large`（50 条超过 24 条上限）。该快照本身不保存全文核验状态，需读取专用状态键；链接或开放获取标记都不等于准入完成。
- 候选专属全文状态键最终为 `failed`（attempt 1）；题录已补全为 `ready`，但 Wiley PDF 来源返回 HTTP 403，错误码 `remote_error` 且 `retryable=false`。因此“全文已核验”为 0 的原因是来源拒绝自动下载，而非队列、题录或网页状态错误；正确恢复路径应为上传有权处理的 PDF 或选择其他文献。
- 再次登录并打开详情页后，页面仍只显示“尚未进入研究集合”，未呈现服务端的 `failed/remote_error/HTTP 403` 或不可重试的“上传有权处理 PDF / 选择其他论文”恢复路径。作为真实前端验收发现，需要核对 `PaperDetailView` 是否根本没有读取候选全文状态，或是状态映射遗漏。
- 根因确认并已修复：详情 API 的候选审核视图会用候选全文状态中的已补全题录呈现 `ready`，但正式引用 API 原先只读主候选快照，因而返回 `citation_not_ready/409`。引用服务现会在主快照未 `ready` 时读取同一运行、同一候选的全文状态，并验证运行与候选标识后才使用其 `ready` 题录。详情页也现对 `failed/rejected` 呈现真实阻断说明；当失败不可重试（本次 HTTP 403）时，显示既有授权上传 PDF 流程。
- 回归通过：后端 Ruff 格式/检查、`test_candidate_citation_service.py`（3 passed）；前端 Prettier、vue-tsc、ESLint，以及 `workflow-shell.spec.ts`（5 passed）。新增浏览器用例断言不可重试 HTTP 403 显示错误、授权上传入口和正式引用预览。
- 真实浏览器刷新后已确认详情页 403 恢复路径生效：显示“选择有权处理的 PDF”和授权上传面板。引用 endpoint 仍重复返回 HTTP 409，说明需要继续区分 API 进程未加载新服务代码和真实状态读取遗漏；下一步直接以当前源码、同一 PostgreSQL/Redis 状态调用服务逻辑诊断。

### 真实浏览器全链路验收收尾（2026-08-04）

- 使用当前源码隔离 API 复验后，正式 GB/T 引用已成功返回；`Sleep, insomnia, and depression` 已完成从单篇核验、加入待确认集合、构建、Embedding 与 Milvus 索引的真实链路。研究运行使用真实 HTTP Reranker（14 个候选重排后返回 6 个），在 11 条主张中发现 1 条证据不足，按契约进入 `awaiting_clarification`，没有生成无依据回答。
- 两个验收临时账号及关联 PostgreSQL、Redis、MinIO 和 Milvus 资源均已精确清理；截图保留在 `output/playwright/sleep-rag-awaiting-clarification-20260804.png`。隔离 Vite、API 与 Playwright 会话均已结束，常驻服务未受影响。
- 前端校验必须使用仓库锁定的 Node `20.19.6` / pnpm `10.34.5`。Codex 内置 Node `24.14.0` / pnpm `11.9.0` 会被 engines 正确拒绝，不能作为前端质量门禁的执行环境。

### 候选相关性评估容量复核（2026-08-04）

- `03-literature-search-and-discovery-discussion.md` 明确要求：一次检索规整后形成唯一统一候选集合，相关性 Agent 在共同上下文中一次返回所有 `candidate_id` 的结构化判断；不得为了展示进度把集合拆成互不知情的串行小批。
- `11-implementation-alignment-discussion.md` 与开发环境说明将 `WORKFLOW_RELEVANCE_COLLECTION_MAX_CANDIDATES` 定义为避免隐式分批的显式容量边界，当前 50 条被标为容量失败正是这一历史约束的表现。
- 用户确认当前模型具有 1M 上下文，要求取消该数量阻断，且不能将修复误做成批处理或串行调用。后续实现应保留“完整候选集合 -> 单次共享上下文 -> 一次结构化全量输出”的形态，只移除候选数量上限及其错误/前端提示；模型超时、输出长度与输入字符的独立保护仍需按实际接口能力审计，不能以候选篇数代替。
- 当前代码证据：`SearchRunExecutor._assess_relevance()` 在 `len(eligible) > workflow_relevance_collection_max_candidates` 时完全跳过 evaluator，并将所有有摘要候选写为 `candidate_relevance_collection_too_large`、不可重试失败；默认阈值为 24。当前 `.env` 未覆盖该项，故真实 50 条检索使用了默认值。对应容量测试也固化了这一旧行为。
- 解除候选数门槛时，还必须提高单次结构化输出预算：默认 `WORKFLOW_RELEVANCE_MAX_OUTPUT_TOKENS=2400` 难以容纳 50 篇的完整结构化理由。该预算是模型生成参数，不应再被误用为候选集合数量限制；需要审计 evaluator 的传参后改为随全量候选规模计算的充足值，同时保留单次调用而不拆批。
- 实现已删除数量阈值及 `candidate_relevance_collection_too_large` 分支；首次相关性判断的输出预算为 `700 * 有摘要候选数`，独立主张核验为 `128 * 有摘要候选数`。当前 50 篇对应 35,000 与 6,400 token，仍分别是一次完整集合调用与一次完整集合核验，不产生候选级串行调用。
- 当前真实 `deepseek-v4-flash` 复验：50 条临时内存候选首次完整执行曾返回一次无效结构化输出，服务端按既有安全契约将其全部拒绝且未持久化；随后以 `include_raw=True` 诊断确认同规模原始 JSON 长度约 29,825 字符、可完整解析，最终再经真实评估器复验为 `50 completed`、无错误码。结论是网关偶发结构化输出异常仍应作为可重试模型失败处理，而不是恢复候选数量限制或串行分批。
- 最终按用户“无需人为限制”的要求，单篇摘要字符截断也已删除：相关性 Agent 的单次共享上下文现在包含完整统一候选集合和每篇完整摘要。保留的仅是模型调用超时、结构化输出校验与按实际有摘要候选数增长的输出 token 请求，它们不会将候选拆批或转为串行处理。

### 讨论稿复核（2026-08-04）

- 复读 `03` 与 `11` 讨论稿后确认：候选的相关性判断和理由主张核验必须各自保持一次完整集合调用；仅全文下载、PDF 校验、准入与向量化属于逐篇异步任务。
- 当前 `SearchRunExecutor` 仅对唯一 `eligible` 集合调用一次评估器；评估器以完整候选 payload 发起一次相关性调用，再以全体有摘要且已有评估的候选发起一次独立主张核验调用。运行代码、配置模板和测试中均无旧 `24` 条上限、摘要字符截断或候选级/批次级循环。
- 浏览器中 `b2f17e2f-a3aa-4750-afc8-864a1bffe04f` 的“超过 24 条”是规则变更前写入 Redis 的历史搜索快照；刷新不能使其重新执行。需要新建检索运行才会走当前完整集合行为。
- 回归复验通过：`backend/tests/unit/test_candidate_relevance.py` 与 `test_search_execution.py` 共 `13 passed`；运行时 `WorkflowSettings` 已确认不存在两项旧限制，工作流 Worker、前端 `5173` 与 API `8000` 均健康。

### 开发环境重启（2026-08-04）

- 已重启 `infra/compose/compose.dev.yml` 的 PostgreSQL、Redis、etcd、Milvus 和 MinIO，五项均恢复 `healthy`。
- 受控 API 已重新在 `8002` 启动；工作流、入库和研究三个 ARQ Worker 均重新启动。前端 `5173` 也已重启，并通过运行时 `VITE_API_BASE_URL=http://127.0.0.1:8002` 连接该受控 API；页面、健康检查和 `5173 -> 8002` CORS 预检均返回 `200`。
- Windows 对旧 `8000` 监听给出的 PID 无法在进程表中归属，因此保留它而未盲目终止；前端不再依赖它。首个 Vite 启动继承项目根目录导致 `404`，已以 `frontend` 为工作目录修复。

### 相关性流式调用真实探针（2026-08-04）

- 对搜索运行 `70e88d14-7a6b-4467-b31d-e4109d584010` 的同一完整 50 条候选输入（其中 25 条有摘要）直接调用当前 `deepseek-v4-flash` 的 JSON 模式流式接口。探针不写 PostgreSQL、Redis 或候选状态。
- 真实结果：首个有效内容片段在 `77.922s` 才抵达，总耗时 `103.172s`；收到了 `14,084` 个流块、其中 `4,862` 个含内容，拼接为 `15,340` 字符 JSON，并成功通过既有 `CandidateRelevanceBatch` 结构校验，得到 25 条评估。
- 结论：流式 JSON 与当前模型/网关/结构化契约兼容。当前 45 秒整次请求超时会在首个流片段前错误中断；后续实现应按首片段与片段间的连接活性处理，而不是按完整集合总运行时长处理，且仍保持一次完整集合调用。

### 流式相关性 Worker 真实复验（2026-08-04）

- `app.workers.relevance.WorkerSettings` 已实际启动，使用 `job_timeout = None` 消费独立 `arq:queue:relevance`。执行器在取得运行锁后立即续期 ARQ in-progress 标记、运行锁和会话 TTL，后续每 30 秒续期；这些租约只用于崩溃恢复和防重领，不用于判定长流失败。
- 运行 `70e88d14-7a6b-4467-b31d-e4109d584010` 的 50 条 Redis 快照经运行级整批重试真实执行。数据库和快照在超过旧 45 秒后仍保持 `running/relevance_assessment`，约 152 秒后完成字段级合并和题录预取；最终统计为 48 完成、1 可重试失败、25 信息不足、12 条题录已预取。
- 唯一失败候选为 `Memory Consolidation`，稳定错误码为 `candidate_relevance_claim_unsupported`：独立核验拒绝其 `level`、`reason`、`helpful_aspect` 的可见主张。未出现流空闲超时、模型总时长超时、ARQ 任务超时、Provider 重检或模型原文写入候选快照/SSE。

### 流 JSON 错误分类与浏览器续验（2026-08-04）

- `CandidateRelevanceStreamPayloadInvalid` 将空流和无法组成 JSON 的流结果归类为 `candidate_relevance_output_invalid`；任何异常文本均不带模型 JSON、推理内容或原文片段。
- 已验证专用 relevance Worker 重启后存在唯一的可归属 ARQ 进程树。此前 Playwright 会话已关闭，不能把失去的登录状态误判为产品链路错误；新的浏览器验收需创建隔离账号并在真实页面验证运行级控制与最终展示。
- 新隔离浏览器运行 `8165b932-d636-42d1-974b-f1d200b52f93` 在真实 50 条候选下完成 Provider 规整与初筛，并显示 21 条相关性完成、29 条可重试。Semantic Scholar 的单源超时仍被隔离；下一步必须从候选结果页核对相关性失败码，不能根据摘要页的汇总文字臆测原因。
- 真实结果页已验证：无摘要候选使用确定性“信息不足”说明，且页面仅暴露运行级整批重试入口。该交互保持完整集合单次调用语义，不会退回单候选串行调用。
- 点击真实运行级重试入口返回 `HTTP 404`。这发生在任务投递前，不能归因于模型或 relevance Worker；需核对前端请求 URL、API 基址和 FastAPI 路由前缀。
- 根因已确认是 `8002` 运行实例的陈旧代码：前端 URL 与当前源码路由完全一致，但该实例的 OpenAPI 不含 retry/cancel 路径。精确重启后 OpenAPI 已包含两个路径，因此不应通过篡改前端 URL 或改变业务契约来规避。
- 重启后的真实浏览器整批重试已跨过 45 秒：具摘要候选仍处于“正在分析”，检查器显示运行级取消操作。页面不公开流块或局部模型判断，符合“完整流完成并通过服务端核验后才展示理由”的边界。
- 同一次完整重试的后续候选页展示了多个已验证的相关层级，并将主张核验拒绝安全显示为“分析未完成”。网络记录只有一次 HTTP 202 的运行级重试和后续候选/运行读取，不包含 Provider 搜索重调。
- 结果页的运行活动判断必须覆盖 `citation_enrichment`，否则在相关性完成而题录预取尚未结束时，用户会看到错误的整批重试入口并失去候选刷新。已将轮询和重试可用性绑定到搜索运行的 queued/running 生命周期；运行级取消仍严格限于 relevance 阶段。
- 该真实运行最终为 `partial_failed/completed`：50 条候选中 48 条相关性分析完成，21 条因摘要缺失被确定性标为信息不足，2 条未通过独立主张核验；题录预取完成 12 条。结论来自运行审计而非页面推断，失败不属于流活动空闲、模型总时长或 ARQ 总时长。
- `test@qq.com` 的运行 `19b46a2b-db9b-46c3-94f0-0452768e3992`：75 条候选中 70 条纳入相关性判断，29 条无摘要而确定性完成，41 条有摘要候选被同一次调用统一标为 `candidate_relevance_model_unavailable`。事件流在进入 `relevance_assessment` 后 `45.280s` 直接完成，没有 relevance 队列任务记录；实际 workflow Worker 的启动时间为 2026-08-04 15:20，早于流式分派代码进入运行时，仍执行旧的内联 45 秒总超时逻辑。当前源码已在 `search_execution.py` 中投递独立 relevance 队列，因此这是陈旧 Worker 未重启造成的运行时版本不一致。
- `test@qq.com` 的运行 `2ba55e7f-05b9-4533-bbb0-f64aa6c892ce` 已使用新流式链路：相关性阶段耗时约 170 秒，49/50 完成。唯一失败候选是 DOI `10.3389/fnins.2022.902925` 的 `Sleep Deprivation During Memory Consolidation, but Not Before Memory Retrieval, Widens Threat Generalization to New Stimuli`；错误码为 `candidate_relevance_claim_unsupported`，原因是模型生成的 `helpful_aspect` 无法由其标题或摘要逐字支持。该拒绝发生在完整集合的独立主张核验阶段，不是流空闲超时、模型不可用或 Provider 失败。
