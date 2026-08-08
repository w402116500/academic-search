# 候选审核持久化与 Redis 职责拆分

## Goal

把候选审核从“Redis TTL 内可用的临时会话”改成“可恢复的业务阶段”。用户完成检索后，即使刷新页面、Redis key 过期、Redis 重启或正常延迟数小时再回来，也应能继续查看候选、保留选择、准备全文并加入研究集合。

本任务不把候选直接升级为长期 `papers` 事实；候选仍然属于 Search 边界，只有用户明确加入研究集合并通过后续准入规则后，才进入集合书目、论文和文档链路。

## Confirmed Facts

- 当前 `SEARCH_SESSION_TTL_SECONDS` 默认是 7200 秒，配置在 `backend/app/core/settings.py:112`。
- `RedisSearchSessionStore.write_snapshot()` 以 Redis string 保存整包 search snapshot，并设置 TTL，见 `backend/app/infra/redis/search_session.py:29`。
- 检索执行把 `status/stage/provider_summary/candidate_counts/candidates` 写入 Redis snapshot，见 `backend/app/modules/search/execution.py:393`。
- PostgreSQL `search_runs` 当前只保存运行头、`redis_session_key`、状态、阶段、provider summary 和 candidate counts，不保存候选列表，见 `backend/app/infra/db/models/workflow.py:138`、`backend/app/infra/db/models/workflow.py:187`、`backend/app/infra/db/models/workflow.py:212`。
- 候选审核读取 Redis snapshot；缺失时会调用 `SearchRunService.expire_run()` 并返回“检索候选已过期，请重新执行文献检索”，见 `backend/app/modules/search/review_session.py:79`、`backend/app/modules/search/run_service.py:245`。
- 候选选择目前保存在 `...:candidate-selection` Redis key，见 `backend/app/modules/search/review_session.py:117`。
- 候选全文状态目前保存在 `...:candidate:{candidate_id}:fulltext` Redis key，见 `backend/app/modules/documents/keys.py:16`、`backend/app/modules/documents/service.py:340`。
- 研究运行进度 Redis Stream 已有正确边界：`RedisResearchEventStore` 只保存短期进度事件，PostgreSQL 仍是权威状态来源，见 `backend/app/infra/redis/research_events.py:14`。
- 运行态抽样显示当前 Redis 中 search snapshot 约 640 KB，selection 约 2 KB；空间不是首要风险，事实放在会过期的位置才是首要风险。
- 历史会话显示早期规划曾明确“PostgreSQL 持久状态与 Redis 短期搜索状态的边界与 TTL”；本任务是有意调整这条边界。

## Key Decisions

- 候选审核事实必须持久化到 PostgreSQL，并随工作区生命周期保存；工作区删除时级联删除。
- 不兼容旧 Redis-only 候选审核数据；项目仍处于开发阶段，迁移只创建新表和空结构。
- 不保存完整 provider 原始响应，也不保存完整多来源合并审计。MVP 只保存审核页、选择、全文准备和准入需要的合并候选事实。
- Redis 继续负责 arq 队列、短期 SSE 进度流、锁/租约和可丢缓存；不能再作为候选审核事实的唯一来源。
- Search 继续拥有候选审核事实，直到用户明确将候选加入研究集合；加入后现有集合书目/论文/文档边界继续生效。

## Requirements

- R1. 检索完成后的候选列表、候选详情、基础初筛、相关性判断、题录补全状态、PDF 可得性状态、用户选择和全文准备状态必须可从 PostgreSQL 恢复。
- R2. 候选审核 API 不得因为 Redis search snapshot 缺失而把已完成或部分完成的 search run 标记为 `expired`。
- R3. 相关性 Worker、全文 Worker 和候选审核 API 可以继续发布/读取 Redis 进度事件和使用 Redis 锁，但所有用户可见和决策相关事实必须先落 PostgreSQL。
- R4. 持久化字段只覆盖用户审核和后续准入需要的合并候选事实：标题、作者、摘要、年份、来源链接、DOI、初筛结果、相关性评估、题录/PDF 可行动状态、最小来源引用等。不保存完整 provider 响应正文或完整合并冲突审计。
- R5. 用户选择必须持久保存，支持勾选、取消勾选、清空、准备和加入研究集合后的幂等取消选择。
- R6. 全文准备状态必须持久保存，支持排队、下载中、校验中、可用、需要上传、失败、重试和上传锁；已获取的暂存 PDF 指针必须能在后续准入时继续使用。
- R7. 前端现有候选审核、选择、全文准备、加入集合的产品交互和 API 形状尽量保持稳定；必要的 API 字段变更必须重新生成 OpenAPI/TypeScript 类型。
- R8. 工作区删除、search run 删除或测试清理必须删除候选审核持久化数据和对应全文状态，不留下可查询的孤立业务事实。

## Acceptance Criteria

- [ ] 已完成或部分完成的 search run 在 Redis 中的 `academic-search:search-run:{run_id}`、`:events`、`:candidate-selection`、`:candidate:{candidate_id}:fulltext` key 被删除后，候选列表和候选详情仍能从 API 正常读取，且 search run 不会被标记为 `expired`。
- [ ] 用户勾选、取消勾选、清空和加入集合后的选择状态在 Redis 丢失或服务重启后仍保持正确。
- [ ] 候选全文准备状态在 Redis 丢失或服务重启后仍能轮询、重试、上传或准入；Redis 锁只影响并发保护，不影响已完成事实恢复。
- [ ] 相关性评估完成后，刷新页面能看到与完成前一致的相关性状态、推荐理由和可核验证据；若 SSE 事件流过期，前端通过普通 API 读取 DB 恢复。
- [ ] Redis 责任收敛到 arq 队列、health-check、短期进度事件、锁/租约和可丢缓存；没有候选审核业务动作依赖 Redis TTL 才能成功。
- [ ] 新 Alembic migration 创建必要表、索引、约束和级联删除关系；不尝试从旧 Redis snapshot 回填历史候选。
- [ ] 持久化数据不包含完整 provider 原始响应或完整多来源合并审计；只包含最小来源引用和用户可见/准入所需字段。
- [ ] 覆盖后端单元测试：检索写入持久候选、相关性更新、候选分页/详情、选择持久化、全文状态持久化、准入和 Redis 缺失恢复。
- [ ] 覆盖前端或 API contract 测试：候选审核页面在普通刷新/重新进入后不展示“候选已过期，请重新执行文献检索”。

## Out of Scope

- 迁移或恢复已经只存在于 Redis 的旧候选审核会话。
- 保存完整 provider 原始响应、HTTP 响应体、完整字段冲突历史或多来源合并审计。
- 改变 `papers`、`collection_papers`、正式文档入库和 RAG 研究阶段的准入规则。
- 解决真实搜索流程曾卡在 relevance `14 / 45` 的 Worker/外部源运行态问题；本任务只消除 Redis TTL 导致的候选审核丢失。
- 单纯调大 `SEARCH_SESSION_TTL_SECONDS` 作为最终修复。

## Open Questions

无阻塞问题。下一步需要用户审批本规划摘要后，才能进入 `task.py start` 和实现。
