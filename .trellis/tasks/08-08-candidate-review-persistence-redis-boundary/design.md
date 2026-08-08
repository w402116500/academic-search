# 候选审核持久化与 Redis 职责拆分设计

## Problem Restatement

用户完成检索后，候选审核应该像研究计划、检索运行和研究集合一样可恢复；当前它被编码成 Redis TTL 内的短期会话，导致业务事实自然过期。

## Principles

- 候选不是 `papers`：未经过用户选择、题录核验和全文准入的记录不能写入长期论文事实表。
- 候选也不是缓存：审核页展示、用户选择、全文准备和加入集合依赖的事实不能只存在于 Redis。
- Redis 适合不要求长期恢复的数据：队列、事件、锁、租约、health-check 和可重建缓存。
- MVP 只保存用户看得见、会影响动作或准入的事实；完整 provider 原始响应和完整合并审计留到以后有真实需求再做。

## Proposed Data Model

新增 Search-owned 候选审核投影，建议放在 workflow/search 相关模型附近，而不是复用 `papers`。

### `search_run_candidates`

一行表示某个 search run 下的一个合并候选。

- 主键/唯一：`(search_run_id, candidate_id)`；`search_run_id` 外键到 `search_runs.id` 并级联删除。
- 排序和筛选：`position`、`title_key`、`published_year`、`relevance_state`、`selected_at`、`created_at`、`updated_at`。
- 展示字段：`doi`、`title`、`language`、`authors` JSONB、`abstract`、`published_date` JSONB、`venue`、`document_type`、`volume`、`issue`、`pages`、`article_number`、`publisher`、`citation_counts_by_source` JSONB、`links` JSONB、`is_open_access`。
- 审核字段：`triage` JSONB、`relevance_assessment` JSONB、`relevance_error` JSONB、`citation` JSONB、`pdf_availability` JSONB。
- 最小来源引用：`source_refs` JSONB，仅保存来源名、来源记录 ID、来源记录 URL、必要的来源计数；不保存完整 provider 响应和完整合并冲突审计。

### `search_candidate_fulltext_states`

一行表示某个 search candidate 的全文准备状态。

- 主键/唯一：`(search_run_id, candidate_id)`；外键到 `search_run_candidates(search_run_id, candidate_id)` 并级联删除。
- 状态字段：`attempt_no`、`status`、`result_document` JSONB、`result_error` JSONB、`arq_job_id`、`requested_at`、`updated_at`。
- `result_document` 可保存现有 `AcquiredFulltext` 投影，包括暂存对象 key、sha256、文件大小和来源 URL；这是后续准入的业务证明，不属于 Redis 临时状态。

选择状态建议先使用 `search_run_candidates.selected_at`，避免为单一布尔动作再建 selection 表。若实现时发现并发或审计需求变强，再拆表。

## Data Flow

1. Search Worker 调用 provider 后，继续执行规整、去重、基础筛选；候选结果写入 `search_run_candidates`，同时可继续向 Redis 写短期 snapshot/event 供 SSE 或兼容 UI 使用。
2. Relevance Worker 通过 Redis relevance lock 控制并发，但从 PostgreSQL 读取候选集合并把每个候选的 `relevance_state/relevance_assessment/relevance_error` 持久更新；progress event 仍写 Redis Stream。
3. 题录补全和 PDF 可得性探测结果写回 `search_run_candidates.citation/pdf_availability`。
4. 候选审核分页、详情、过滤和统计改为查询 PostgreSQL；Redis snapshot 缺失不触发 `SearchRunService.expire_run()`。
5. 选择更新改为 PostgreSQL 行更新；`candidate-selection-lock` 可删除或只作为可选并发保护，不能再承载事实。
6. Fulltext API 和 Worker 通过新的持久状态仓储读写 `search_candidate_fulltext_states`；Redis 只保留 upload lock 和 arq 队列。
7. Admission 继续使用现有 `CollectionBibliographyRepository.upsert_from_candidate()`，但候选来源改为持久候选投影；成功加入集合后将对应候选 `selected_at` 清空。
8. Search SSE 仍可从 Redis Stream 推送实时进度；事件流过期或断线时，前端刷新普通 API 从 DB 恢复最终状态。

## Boundary Changes

- `RedisSearchSessionStore` 文档和接口应从“候选事实存储”收窄为“短期事件、锁和可丢缓存存储”。
- `CandidateReviewSession` 当前混合了 run ownership、Redis snapshot、selection 和 fulltext state；实现时应拆成面向持久候选的查询/命令端口，保留 owner check。
- `SearchRunStatus.EXPIRED` 不再因为已完成 search run 的候选 snapshot 过期而产生。该状态可以保留给旧 API 枚举或真正无法恢复的历史状态，但候选审核新链路不主动写入。
- `CollectionBibliographyEntry.source_candidate_id` 注释里的“来源 Redis 候选标识”应更新为“来源检索候选标识”。

## Compatibility and Migration

- 新 migration 只创建空表和必要索引/约束，不从 Redis 回填旧数据。
- 开发阶段可以接受现有 Redis-only search run 在新代码下无法恢复候选；若本地数据库里已有旧 run，可重新检索。
- `search_runs.redis_session_key` 暂时保留，用于事件流 key、锁 key 和过渡期兼容；不作为候选审核事实的必要条件。

## Trade-offs

- 空间：每个候选保存标题、摘要、作者、相关性判断和最小来源引用，会比 Redis TTL 临时数据占用更多 PostgreSQL 空间，但当前实测单个 snapshot 约 640 KB，数量级可控。
- 可观测性：不保存完整 provider 响应和完整合并审计会降低事后排查“为什么这样合并”的能力；MVP 接受该损失，换取更小存储和更清晰边界。
- 一致性：相关性/题录/PDF 状态写 DB 后，Redis event 可能丢失，但普通 API 可恢复；这是目标行为。

## Rollback Considerations

- migration 应可 downgrade 删除新增表；由于不迁移旧 Redis 数据，回滚不会需要反向转换。
- 实现期间保留旧 Redis event/lock 路径，降低 worker 调度风险。
- 若持久候选写入失败，search run 应进入失败或部分失败的可解释状态，而不是只发布 Redis 错误。
