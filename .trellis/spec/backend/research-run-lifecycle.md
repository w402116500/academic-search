# Research Run Lifecycle

## Scenario: Cancellation And Worker Restart Recovery

### 1. Scope / Trigger

Use this contract when changing research question queueing, `ResearchRun`
status transitions, cancellation, Worker startup, terminal SSE publication, or
frontend pending-run recovery. The invariant is that PostgreSQL is the durable
source of truth; Redis Stream events are replay hints and cannot be the only
way a run reaches a terminal state.

### 2. Signatures

Durable run fields:

```text
ResearchRun
  status = queued | running | awaiting_clarification | completed | failed | cancelled
  stage = dispatch | preparing | ... | completed | awaiting_clarification | failed | cancelled
  cancel_requested_at: datetime | None
  output_message_id: UUID | None
  retrieval_trace.cancellation.state = requested | confirmed
```

Execution adapter recovery entry:

```python
async def finalize_requested_cancellations(self) -> tuple[UUID, ...]: ...
```

Worker startup must call the recovery entry after dependency construction and
publish a terminal `ResearchProgressEvent(status=cancelled, stage=cancelled)`
for each recovered run on a best-effort basis.

### 3. Contracts

Queued cancellation is immediate: set `status=cancelled`, `stage=cancelled`,
`cancel_requested_at`, and `finished_at`.

Running cancellation is cooperative at request time: set `cancel_requested_at`
and `retrieval_trace.cancellation.state="requested"` while leaving
`status=running`. The in-flight Worker should finalize the run at the next safe
boundary.

Worker restart recovery must finalize any `running` run that already has
`cancel_requested_at` set. This prevents a process restart, model timeout, or
lost arq in-flight job from leaving the frontend permanently blocked by
`running/preparing` with no output message.

Completion and failure persistence must continue to treat
`cancel_requested_at` as stronger than a late answer or exception: if the run
has a cancellation request, mark it `cancelled` and do not create an assistant
message or evidence records.

### 4. Validation & Error Matrix

| Condition | Action |
| --- | --- |
| `queued` run is cancelled | Persist `cancelled` immediately |
| `running` run receives first cancel request | Persist `cancel_requested_at`; keep `running` until Worker boundary or restart recovery |
| Worker reaches a safe boundary after cancellation | `finalize_cancellation()` closes the active timing stage and marks `cancelled` |
| Worker starts and finds `running + cancel_requested_at` | `finalize_requested_cancellations()` marks `cancelled` and emits terminal SSE best-effort |
| Late `complete()` sees `cancel_requested_at` | Mark `cancelled`; do not write assistant message/evidence |
| Late `fail()` sees `cancel_requested_at` | Mark `cancelled`; do not write failed error as final user-visible state |
| Redis terminal event publish fails during recovery | Keep PostgreSQL terminal state; log safe diagnostics only |

### 5. Good / Base / Bad Cases

- Good: user cancels a long strict research call, the Worker is restarted, and
  startup recovery turns the run into `cancelled` so the composer is unlocked.
- Base: user cancels while the Worker is healthy; the next stage boundary
  confirms cancellation and publishes a terminal SSE event.
- Base: a cancelled run's late model result returns after the DB terminal state;
  the late `complete()` path is ignored or converts to `cancelled`, never an
  answer.
- Bad: relying only on Redis Stream terminal events; a Worker restart can lose
  the event while PostgreSQL still says `running`.
- Bad: leaving `running + cancel_requested_at + output_message_id=null`
  indefinitely; the frontend treats it as a pending run and disables new
  questions.

### 6. Tests Required

- Unit-test `finalize_cancellation()` closes the active stage and writes
  `retrieval_trace.cancellation.state="confirmed"`.
- Unit-test `finalize_requested_cancellations()` recovers
  `running + cancel_requested_at` runs after an interrupted Worker.
- Unit-test `complete()` / `fail()` do not publish answers or failed terminal
  state when a cancellation request exists.
- Browser or API regression should verify there are no stale
  `queued` / `running` research runs after restarting the Research Worker.

### 7. Wrong Vs Correct

#### Wrong

```python
if run.status == "running":
    run.cancel_requested_at = now
    return running_response
```

This is incomplete if the Worker process disappears before the next safe
boundary; the user-facing conversation can stay blocked forever.

#### Correct

```python
run.cancel_requested_at = now

# Worker boundary or Worker startup:
if run.status == "running" and run.cancel_requested_at is not None:
    mark_cancelled(run)
```

Cancellation is cooperative during the live call, but every cancelled run must
eventually converge to a PostgreSQL terminal state.

## 场景：工作区永久删除围栏

### 1. 范围 / 触发条件

当变更工作区永久删除、入库或研究任务取消、工作区私有对象或向量清理、
`DELETE /api/v1/collections/{collection_id}` 时，必须遵守本契约。删除不是
HTTP 进程内的队列操作：PostgreSQL 中的工作区状态是所有新写入和 Worker
晚到持久化的唯一持久闸门。

### 2. 签名

```python
class WorkspaceDeletionRepository(Protocol):
    async def begin_deletion(
        self, *, owner_user_id: UUID, workspace_id: UUID
    ) -> WorkspaceDeletionSnapshot | None: ...

    async def has_running_ingestion(self, *, workspace_id: UUID) -> bool: ...
    async def has_running_research(self, *, workspace_id: UUID) -> bool: ...
    async def delete_root(self, *, owner_user_id: UUID, workspace_id: UUID) -> bool: ...

async def delete(*, owner_user_id: UUID, collection_id: UUID) -> None: ...
```

对外接口为 `DELETE /api/v1/collections/{collection_id}`，成功返回
`204 No Content`。`WorkspaceDeletionSnapshot` 固定包含该工作区的
`ingestion_run_ids` 和 `document_object_keys`，不包含共享 `Paper` 事实。

### 3. 契约

- `begin_deletion` 在短事务内验证所有权，把 `active` 或 `archived` 工作区
  置为 `deleting`，请求全部未终态任务取消，并返回同一份可重试的清理快照。
- `deleting` 工作区不得接受常规读取、写入、claim、progress 或 finalize 路径的
  持久化写入。工作区列表应向所有者返回该状态，以提供受控的“继续删除”恢复入口；
  它不得作为可打开或可编辑的普通工作区。
- 服务必须先等待研究和入库任务均达终态，再按 ingestion run UUID 删除向量、
  删除私有对象键。物理删除时，仓储须在同一事务显式删除该工作区的
  `ResearchEvidence`、再删除 `ResearchRun`，最后删除工作区根记录；普通审计路径
  的 `RESTRICT` 外键不得改为全局 `CASCADE`。
- 外部清理失败或等待超时后根记录必须保留为 `deleting`；重复 DELETE 从该
  状态继续，不能重新开放工作区。对用户只展示统一的删除未完成提示；错误码、资源
  类型和原始异常记录为后端诊断日志。前端成功后删除所有含该工作区 UUID 的查询
  缓存并失效工作区列表；删除当前路由的工作区时跳回研究入口。

### 4. 校验与错误矩阵

| 条件 | 服务端行为 | HTTP |
| --- | --- | --- |
| 工作区不存在或不属于当前用户 | 不泄漏归属，拒绝删除 | 404 |
| 首次删除活动或归档工作区 | 持久化 `deleting`、请求取消并开始屏障 | 成功时 204 |
| 重试 `deleting` 工作区 | 复用持久快照继续清理 | 成功时 204 |
| 任一任务尚未终态且已到等待上限 | 保持 `deleting`，不删除外部资源或根记录，记录诊断 | 409 `deletion_in_progress` |
| 向量、对象或数据库私有记录清理失败 | 保持 `deleting`，不删除根记录，记录诊断 | 503 `deletion_cleanup_failed` |
| 根记录已被并发删除 | 初始请求已验证所有权时视为同次删除成功 | 204 |

### 5. 正常 / 基础 / 反例

- 正常：用户删除含多个入库运行的工作区，Worker 协作取消完成后，所有对应
  向量、私有全文对象和级联私有记录被清理，而共享论文仍存在。
- 基础：用户重复点击删除或在研究入口的待完成删除区域继续删除，服务保持
  `deleting` 并从既有快照继续，不创建新的工作区状态或重新允许写入。
- 基础：删除当前工作区后，前端移除其私有缓存并 `replace` 到研究入口；删除
  其他工作区时保留当前页面并刷新侧栏。
- 反例：先删除向量再取消 Worker。晚到入库可能重新写入向量，造成已删除
  工作区的孤儿数据。
- 反例：用 `asyncio.gather` 并发调用同一仓储的两个查询。请求级
  `AsyncSession` 不是可并发使用的连接，必须顺序轮询或使用独立会话。

### 6. 必需测试

- 服务层测试首次删除、重试、等待中的任务、对象或向量清理失败，以及共享
  `Paper` 不在删除目标中。
- 回归测试验证共享 `AsyncSession` 的仓储替身下，运行状态查询按顺序执行。
- API 契约测试验证所有权和不存在统一 404、屏障未完成为 409、清理失败为
  503、成功为 204，并重新生成 OpenAPI 产物。
- 浏览器测试验证确认前不发 DELETE、确认框展示名称与不可恢复提示、成功后
  跳入口；删除未完成时显示通用提示，侧栏和研究入口均可继续删除。
- 可选 live PostgreSQL 验收必须显式 opt-in；默认测试不得读取本地凭据或启动
  基础设施。

### 7. 错误与正确示例

#### 错误

```python
await asyncio.gather(
    repository.has_running_ingestion(workspace_id=workspace_id),
    repository.has_running_research(workspace_id=workspace_id),
)
await vector_index.delete_ingestion_run(run_id)
```

这会并发使用同一个请求级会话，且没有先证明 Worker 已停止，晚到任务仍可能
在向量删除后写入。

#### 正确

```python
has_running_ingestion = await repository.has_running_ingestion(
    workspace_id=workspace_id
)
has_running_research = await repository.has_running_research(
    workspace_id=workspace_id
)
if not has_running_ingestion and not has_running_research:
    await vector_index.delete_ingestion_run(run_id)
```

顺序读取在同一 `AsyncSession` 上安全；只有持久化删除围栏已建立且所有任务
到达终态后，才可以清理外部资源并删除工作区根记录。
