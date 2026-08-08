# Candidate Review Persistence

## 场景：候选审核持久化与 Redis 职责边界

### 1. 范围 / 触发条件

当修改检索候选生成、候选审核 API、用户选择、相关性评估、候选全文准备、
候选题录/准入、Redis search session、search run 恢复或工作区删除时，必须遵守本契约。
本场景包含数据库 schema、Worker、API 和前端恢复路径，属于跨层/基础设施契约。

核心不变量：候选审核事实属于 Search 边界内的 PostgreSQL 持久投影；Redis 只能作为
arq 队列、短期进度事件、锁/租约、health check 和可丢缓存，不能成为候选审核事实或
完成状态的唯一证据。

### 2. 签名

持久表：

```text
search_run_candidates
  primary key (search_run_id, candidate_id)
  search_run_id -> search_runs.id on delete cascade
  position
  doi, title, title_key, language, authors, abstract
  published_year, published_date, venue, document_type
  volume, issue, pages, article_number, publisher
  citation_counts_by_source, links, is_open_access
  source_refs
  triage
  relevance_state, relevance_assessment, relevance_error
  citation, pdf_availability
  relevance_retry_attempt_no
  selected_at

search_candidate_fulltext_states
  primary key (search_run_id, candidate_id)
  (search_run_id, candidate_id) -> search_run_candidates on delete cascade
  attempt_no, status
  candidate
  result_document, result_error
  arq_job_id
  requested_at, state_updated_at
```

Search-owned port：

```python
class SearchCandidateRepository(Protocol):
    async def upsert_candidates(
        self, *, search_run_id: UUID, candidates: Sequence[UnifiedCandidate]
    ) -> None: ...
    async def list_candidates(self, *, search_run_id: UUID) -> tuple[UnifiedCandidate, ...]: ...
    async def get_candidate(
        self, *, search_run_id: UUID, candidate_id: UUID
    ) -> UnifiedCandidate | None: ...
    async def selected_ids(self, *, search_run_id: UUID) -> set[UUID]: ...
    async def set_selected(
        self, *, search_run_id: UUID, candidate_ids: Sequence[UUID], selected: bool
    ) -> int: ...
    async def clear_selection(self, *, search_run_id: UUID) -> None: ...
    async def write_fulltext_state(self, state: CandidateFulltextState) -> None: ...
    async def get_fulltext_state(
        self, *, search_run_id: UUID, candidate_id: UUID
    ) -> CandidateFulltextState | None: ...
```

`search_runs.redis_session_key` 可以继续保存短期事件/锁/缓存 key，但不是读取候选审核
事实的必需字段。`collection_bibliography_entries.source_candidate_id` 表示来源检索候选
ID，不表示 Redis 候选记录。

### 3. 契约

- Search Worker 在 provider 检索、规整、去重和初筛后，必须先把合并候选写入
  `search_run_candidates`。Redis snapshot 只能是短期缓存或事件辅助。
- 候选持久化只保存用户可见、会影响动作或准入的合并事实，以及最小 `source_refs`。
  不保存完整 provider 原始响应、HTTP body、Cookie、凭据或完整合并冲突审计。
- 相关性、题录补全、PDF 可得性和候选全文准备状态都写回 PostgreSQL。Redis event
  丢失时，普通 API 必须能从 PostgreSQL 恢复页面状态。
- 用户选择由 `selected_at` 表示。勾选、取消、清空、加入集合后的幂等取消选择都必须
  更新 PostgreSQL；Redis selection key 不得作为事实来源。
- 候选全文状态由 `search_candidate_fulltext_states` 表示。Redis upload lock 只保护并发，
  arq job id 只用于调度追踪；已完成的 `result_document` 和终态错误摘要必须可从
  PostgreSQL 恢复。
- 已完成或部分完成的 `SearchRun` 不得因为 Redis snapshot、event stream、selection key
  或 candidate fulltext key 缺失而被标记为 `expired`。`expired` 只能用于真实不可恢复的
  历史状态或明确设计的过期状态。
- 候选仍然不是长期 `Paper` 事实。只有用户明确加入研究集合并通过题录、全文和权限准入
  后，才能进入集合书目、论文和文档链路。
- 开发阶段不兼容旧 Redis-only 候选数据；schema 迁移只创建空表和约束，不从 Redis 回填。

### 4. 校验与错误矩阵

| 条件 | 行为 |
| --- | --- |
| 完成/部分完成 run 的 Redis snapshot 缺失 | 候选审核 API 从 PostgreSQL 读取；不得调用 `expire_run()` |
| Redis event stream 过期或发布失败 | 保留 PostgreSQL 状态；记录安全诊断，前端通过普通 API 恢复 |
| 候选 ID 不属于当前 search run | 返回候选不存在/不可操作错误；不得跨 run 查询或写入 |
| 选择更新包含不存在候选 | 只更新属于该 run 的候选，并用更新数量/领域错误暴露异常输入 |
| 相关性 retry membership 与待处理候选不一致 | 回退到当前 pending included 候选集合，不重算已终态候选 |
| 全文 upload lock 冲突 | 拒绝或等待本次并发上传；不得覆盖已持久化的终态全文状态 |
| 持久候选写入失败 | search run 进入可解释失败或部分失败；不得只发布 Redis 错误后继续 |
| 工作区或 search run 删除 | 级联删除候选行和候选全文状态，不留下可查询孤儿事实 |

### 5. 正常 / 基础 / 反例

- 正常：检索完成后删除 Redis search snapshot、events、selection 和 candidate fulltext key，
  用户刷新页面仍能看到候选、相关性理由、选择和全文准备状态。
- 基础：实时 SSE 还在时，Redis event 让页面更快更新；断线或事件过期后，页面重新查询
  API 得到同一 PostgreSQL 事实。
- 基础：用户把候选加入研究集合后，对应 `selected_at` 被清空；集合书目使用来源检索候选
  ID 追溯来源，但正式 `Paper` 仍由准入链路创建。
- 反例：候选审核 API 读不到 Redis snapshot 就把 completed run 改成 `expired`。
- 反例：把完整 provider 原始响应或多来源合并审计写入候选表，制造不必要存储和隐私风险。
- 反例：把候选提前写入 `papers`，绕过 DOI、题录、全文和权限准入。

### 6. 必需测试

- 检索执行测试必须断言 triage 后候选被写入 `SearchCandidateRepository`，且只保存最小来源引用。
- 相关性执行测试必须断言评估结果、终态失败和 retry membership 写入 PostgreSQL，Redis 缺失不影响
  retry 子集恢复。
- 候选审核服务/API 测试必须覆盖列表、详情、选择、清空、准入后取消选择，以及 Redis key 缺失时不
  标记 run 为 `expired`。
- 全文服务/Worker 测试必须覆盖状态排队、下载中、可用、需要上传、失败、重试和上传锁边界；已完成
  `result_document` 必须能从 PostgreSQL 恢复。
- 数据库模型/迁移测试必须覆盖复合主键、级联删除、JSONB 类型约束、`source_candidate_id` 语义和
  Alembic upgrade/check。
- API/OpenAPI/前端测试只在 wire shape 变化时更新生成产物；前端不得把 Pinia 或页面内存作为候选完成
  状态来源。

### 7. 错误与正确示例

#### 错误

```python
snapshot = await redis_session_store.get_snapshot(session_key)
if snapshot is None:
    await search_run_service.expire_run(run_id=run.id)
    raise CandidateReviewExpiredError
```

这把 Redis TTL 当成候选审核事实的生命周期，完成的业务阶段会自然丢失。

#### 正确

```python
candidate = await candidate_repository.get_candidate(
    search_run_id=run.id,
    candidate_id=candidate_id,
)
if candidate is None:
    raise CandidateNotFoundError
```

候选审核事实从 Search-owned PostgreSQL 投影恢复；Redis 只影响实时性或并发保护，不决定
已完成 run 是否仍可审核。
