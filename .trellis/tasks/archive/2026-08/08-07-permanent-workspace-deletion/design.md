# 工作区永久删除设计

## 删除不变量

1. 工作区状态是所有新写入的唯一持久闸门。删除开始后，状态变为 `deleting`，常规用例不能读取或写入；所有者的工作区列表保留一个不可导航的恢复行。
2. 删除完成屏障前，任何已领取 Worker 都必须观察到删除请求并到达可证明的终态；不能仅依赖 HTTP 进程内存或队列移除。
3. 屏障通过后，先删除每个 ingestion run 的向量和每个私有对象键。物理删除事务显式删除工作区的 `ResearchEvidence`、再删除 `ResearchRun`、最后删除集合根记录；共享 `Paper` 与普通审计路径的 `RESTRICT` 外键保持不变。
4. 外部或数据库私有记录清理失败时保持 `deleting`，不返回成功；重复 `DELETE` 会从持久状态继续清理，不能重新开放工作区。用户只看到统一提示，细节写入后端日志。

## 所有者与端口

- 业务用例归 `modules/research`：新增删除 command、状态/错误契约和 `WorkspaceDeletionRepository` 端口。
- SQLAlchemy adapter 归 `infra/db/repositories`：原子开始删除、列出受影响运行与对象键、请求取消、查询终态、物理删除根集合。
- RAG 向量删除通过已有 `DocumentChunkVectorIndex.delete_ingestion_run`；对象删除通过 `ResearchDocumentObjectStorage.delete_object`。用例显式接收端口，不反向依赖基础设施。
- API 只负责当前用户、错误映射和 HTTP 响应；deps 组装 SQLAlchemy、对象存储和向量适配器。

## 状态和时序

1. `begin_deletion` 在短事务内锁定所属集合，将 `active` 或 `archived` 置为 `deleting`，请求所有未终态运行取消，并返回清理快照。重复请求接受 `deleting` 并返回相同快照。
2. 删除用例以有限轮询等待数据库运行记录进入终态。每轮短暂释放数据库事务，让 Worker 完成取消；超时为明确的未完成错误，状态仍为 `deleting`。
3. Worker 的 claim/progress/finalize 路径以集合状态和取消标记为围栏：未领取任务终态取消，已领取任务在下一安全点停止，后续持久化不能把已取消运行复活。
4. 屏障成功后，以 run UUID 精确删除 Milvus 向量，删除快照内的文档对象，再在短事务中依次删除证据、研究运行和集合根记录。
5. 任一步外部清理失败都不删除根记录；重试保持幂等。根记录已物理删除时，后续 DELETE 遵循 404 所有权语义。

## 前端与 API

- 添加 `DELETE /collections/{collection_id}`，成功返回 `204 No Content`。
- OpenAPI 生成后，前端由 generated schema 获得操作类型；在研究 API hook 中封装 mutation，成功后移除/失效所有该工作区 key。
- `WorkspaceFrame` 的普通工作区行增加带 tooltip 的删除图标。点击不会导航；确认对话框展示名称、不可恢复提示、取消与删除动作。
- `deleting` 行禁用导航与普通删除确认，只显示状态和“继续删除”动作；研究入口提供紧凑的“待完成删除”恢复区，直接继续而不重复确认。
- 删除当前工作区成功后路由至入口；删除其他工作区后留在当前路由并刷新侧栏。

## 故障语义

- 未找到、非所属和已物理删除统一为 404。
- 删除进行中而尚未达屏障或清理失败返回 409/503，前端展示同一条删除未完成提示并重新拉取列表；后端记录可溯源的诊断信息，不伪造成功。
- 不引入回收站、恢复或按名称输入确认。
