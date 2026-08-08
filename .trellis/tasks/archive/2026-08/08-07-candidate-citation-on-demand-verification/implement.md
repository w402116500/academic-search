# 实施计划：候选自动核验、研究集合与 RAG 范围

## 实施顺序

1. 建立持久化边界
   - 在 `modules/research` 定义集合书目条目、内容状态、删除命令、列表响应和端口。
   - 在 `infra/db/models`、repository 与 Alembic 新 revision 中实现条目表、Document 关联、
     历史回填和精确删除所需的查询。
   - 扩展集合列表摘要，同时保持 `documents` 作为已取得 PDF 的兼容投影。

2. 拆开候选自动就绪
   - 删除 `CandidateRelevanceRunExecutor` 的固定题录预补全限额，改为审核池全量、受限并发的
     逐项题录 enrichment。
   - 在 `modules/documents` 实现不下载文件的安全 PDF 探测，并将题录/PDF 结果作为候选快照的
     两个独立字段原子合并。
   - 更新 search stage、计数、Worker 装配、结构化日志和候选审核 API；移除旧的准备核验
     API contract 与前端依赖。

3. 将“加入研究集合”改为唯一确认命令
   - 改造 search/research admission use case：读取 Redis 已选候选，幂等创建所有集合书目条目，
     不以题录、DOI 或 PDF 状态阻塞，并只清除成功持久化的选中 ID。
   - 为可自动获取 PDF 的条目投递持久化下载任务；实现一次自动重试和最终 `requires_upload`。
   - 将成功下载/上传串接至文档提升与 ingestion 投递，保证 RAG 只查询 current completed
     ingestion run。

4. 收紧 PDF 与删除生命周期
   - 把下载/上传前的题录 hard gate 改为候选 DOI/标题与 PDF 可提取标识的明确冲突校验。
   - 新增持久化集合条目上传 API 与单条目删除 API；删除时停止相关任务并清理对象、解析、
     向量和入库记录，不删除共享 `Paper`。
   - 让下载、上传、入库 Worker 在 claim/progress/finalize 阶段遵守集合/条目删除围栏。

5. 改造候选与集合工作台
   - 用 generated OpenAPI DTO 更新 `api/`, query keys 和 hooks，移除 prepare/retry/build 操作。
   - 重构 `ResultsView`、`CandidateReviewTable` 与候选详情：状态可读、可选、唯一加入操作、
     不泄漏内部错误。
   - 重构 `CollectionView` 为“全部集合书目 + RAG 研究范围”并列布局，提供持久化条目的上传与
     删除，不展示手动下载/题录重试。
   - 使用真实浏览器在桌面和窄屏检查候选页、需上传、自动入库、范围已可研究和删除状态。

6. 生成与集成检查
   - 生成 OpenAPI 产物，处理相邻范围详情/回答引用任务对集合响应的兼容编译问题。
   - 只在本任务的相关文件中处理冲突；已有未提交的工作区删除、范围详情和回答展示改动不回退。

## 关键测试接缝

- `CandidateRelevanceRunExecutor`：审核池候选全部收到题录与 PDF 探测，固定上限不存在；单项
  Provider 故障不会中止同批其他候选；并发和自动重试有上界。
- Documents：探测不下载/不暂存 PDF；下载/上传不依赖题录 ready；PDF 明确 DOI/标题冲突被拒绝，
  标识不可提取的有效 PDF 被接受。
- Research admission：题录不可用和需上传的候选仍创建集合书目条目；幂等加入不复制条目；
  可下载条目自动投递且下载失败仅自动再试一次。
- Ingestion/RAG：仅当前已完成入库的文档参与范围与检索；上传/下载完成后自动进入范围；
  删除条目清理其 document、ingestion、对象和向量。
- HTTP/OpenAPI：候选审核和集合列表返回新稳定状态；移除的用户动作不可再经路由调用；上传、
  加入和删除保持所有权检查。
- Frontend：候选详情不显示技术错误，题录/PDF 状态不阻塞选择；集合与 RAG 范围计数不同；
  窄屏文本、上传和删除控件不溢出。

## 验证命令

开发中每个 coherent batch 只运行最近的测试文件。跨层完成后运行一次：

```powershell
uv run --directory backend ruff check .
uv run --directory backend ruff format --check .
uv run --directory backend pyright
uv run --directory backend lint-imports --config ../.importlinter
uv run --directory backend python ../scripts/check_source_size.py
uv run --directory backend pytest tests/unit/test_candidate_relevance_execution.py tests/unit/test_candidate_review_service.py tests/unit/test_candidate_fulltext_service.py tests/unit/test_collection_build_service.py

pnpm --dir frontend api:check
pnpm --dir frontend format:check
pnpm --dir frontend lint
pnpm --dir frontend typecheck
pnpm --dir frontend test:unit
```

再以已有 API/Worker 实例进行相关 Playwright 场景，不启动重复 API 或 Research Worker：候选自动
就绪、加入集合后自动入库、需上传后入库、集合与 RAG 范围分离、删除集合条目。只有改动直接
影响的 live 边界在用户明确要求时才执行。

## 高风险点与检查

- 数据库 migration 必须 upgrade/downgrade 可执行，且不原地修改旧 revision；先检查开发库内
  历史 `CollectionPaper`/`Document` 可回填。
- 队列投递和数据库状态不得形成“已显示排队但没有可领取 job”的伪成功。投递失败必须进入
  有界系统重试或最终稳定的需上传状态并记录错误码。
- 新 `CollectionBibliographyEntry` 是集合书目唯一计数来源，`IngestionRun.is_current && completed`
  是 RAG 范围唯一计数来源；任何页面不得从 Redis 或本地计数推断二者。
- 当前工作区有相邻任务未提交的 `services.py`、集合模型、ingestion repository、OpenAPI、
  research hooks 与研究视图改动。实施前逐文件审阅 diff，并在其事实所有者处合并，不覆盖用户
  已完成的删除和范围详情工作。
