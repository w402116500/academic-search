# Findings & Decisions

## Requirements

- 目标链路是“提交研究要求 -> 创建工作区草稿 -> 意图分析 -> 用户确认研究计划 -> 多源检索 -> 统一文献结果”。
- 前端原型已经按连续阶段设计，后端需要提供可恢复的服务端状态，而不是只返回一次性 JSON。
- MVP 不做付费功能；本阶段不实现 RAG 研究问答和复杂多 Agent。
- 工作流状态必须有清晰的中文代码注释、数据库列注释和接口展示说明；不能只给出英文机器值。

## Research Findings

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

## Issues

| Issue | Resolution |
|-------|------------|
| 文档曾将“RAG 入库 Worker 未实现”与实际代码混用 | 以 RAG 讨论稿和 `backend/app/workers/ingestion.py` 为准，区分 Worker 已实现与自动投递 API 未实现 |
| 前端文档曾写确认后才创建工作区 | 已统一为提交要求时创建 `draft` 工作区，确认计划后才启动检索 |
| 工作流初版误建唯一索引 | ORM 的 `unique=True` 需要唯一约束；新增 `f41c8e7b2a06` 修正且保持数据不变 |
| 首次真实 JSON mode 输出使用自定义包装字段 | 提示词补充精确 JSON 形状后，真实模型已返回可通过 `ResearchPlanDraft` 校验的 3 个方向和对应查询计划 |

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

## Phase 4 Verification

- 离线测试：98 个单元测试通过，Ruff、格式化、Pyright 和 `alembic check` 通过；完整测试集为 101 passed、4 skipped。
- 真实测试：`RUN_LIVE_SEARCH_RUN_TESTS=1 uv run pytest tests/integration/test_live_search_run.py -m live -s` 通过。
- 真实结果：OpenAlex、Crossref、arXiv、Semantic Scholar 均返回成功；75 条原始候选规整去重后得到 57 条候选，运行状态为 `completed`。
- 真实数据边界：候选和事件只写入 Redis 短期会话，`papers` 没有被搜索运行直接写入；临时用户、工作区、计划和运行已清理。
- Milvus 可见性：向量 upsert 后显式 flush，避免入库完成到首次检索之间因最终一致性出现空结果。
