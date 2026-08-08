# 候选审核持久化与 Redis 职责拆分实施计划

## Before Coding

- 进入实现前先运行/遵循 `trellis-before-dev`，读取 backend、frontend、database、testing 相关 spec。
- 本任务按 inline 实施规划；若后续改为 sub-agent dispatch，再补充真实的 `implement.jsonl` 和 `check.jsonl` curated entries。

## Implementation Checklist

1. 数据库迁移与模型
   - 新增 `search_run_candidates` 和 `search_candidate_fulltext_states`。
   - 增加复合唯一、外键级联、常用分页/筛选索引和 JSONB 类型约束。
   - 更新 SQLAlchemy models、`__init__` 导出和相关注释。

2. 持久候选端口与仓储
   - 定义 Search-owned candidate review repository/ports。
   - 支持批量 upsert、按 run 分页/过滤/详情、批量更新相关性/题录/PDF 状态、选择更新、全文状态读写。
   - 提供 `UnifiedCandidate` 与持久行之间的显式映射，持久化时裁剪 `source_records` 为最小 `source_refs`。

3. 检索与相关性执行链路
   - `SearchRunExecutor` 在 triage/relevance preparation 阶段写入持久候选。
   - `CandidateRelevanceRunExecutor` 改为从 DB 读取候选并把评估结果写回 DB。
   - Redis snapshot 写入降级为可丢缓存或逐步移除；Redis Stream 继续用于进度事件。

4. 候选审核 API
   - `CandidateReviewQueryService` 改为从 PostgreSQL 读取候选、选择和全文状态。
   - `CandidateSelectionService` 改为持久更新 `selected_at`。
   - `CandidatePreparationService` 和 `CandidateAdmissionService` 使用持久候选事实，不再要求 Redis snapshot 存在。
   - 移除候选审核路径上的 `expire_run()` 触发。

5. 全文准备链路
   - `CandidateFulltextService` 和 `workers/fulltext.py` 改为读写持久全文状态。
   - Redis 仅保留 upload lock、队列和必要租约。
   - 确认已获取的暂存 PDF 指针能跨刷新和 Redis 重启继续准入。

6. 前端和 API 类型
   - 保持现有候选审核 UI 行为；如 OpenAPI schema 变化，运行类型生成。
   - 更新“过期”相关文案或状态分支，避免正常完成的候选审核显示重跑检索。

7. 测试
   - 新增/更新单元测试覆盖 Redis 缺失恢复、选择持久化、全文状态持久化、相关性写 DB、准入后取消选择。
   - 更新 API contract 和前端测试，确认刷新后仍可回到候选审核页面。

## Focused Validation

```powershell
uv run --directory backend alembic upgrade head
uv run --directory backend alembic check
uv run --directory backend pytest tests/unit/test_search_execution.py tests/unit/test_candidate_relevance_execution.py tests/unit/test_candidate_review_service.py tests/unit/test_candidate_fulltext_service.py tests/unit/test_collection_admission.py
corepack pnpm --dir frontend api:check
corepack pnpm --dir frontend test:unit
corepack pnpm --dir frontend test:e2e -- tests/e2e/candidate-review.spec.ts tests/e2e/candidate-upload.spec.ts
```

## Full Quality Gate

```powershell
uv run --directory backend ruff check .
uv run --directory backend ruff format --check .
uv run --directory backend pyright
uv run --directory backend lint-imports --config ../.importlinter
uv run --directory backend alembic check
uv run --directory backend pytest
corepack pnpm --dir frontend format:check
corepack pnpm --dir frontend lint
corepack pnpm --dir frontend typecheck
corepack pnpm --dir frontend test:unit
corepack pnpm --dir frontend test:e2e
git diff --check
```

## Risky Files

- `backend/app/infra/db/models/workflow.py`
- `backend/app/infra/db/repositories/search_runs.py`
- `backend/app/modules/search/execution.py`
- `backend/app/modules/search/relevance_execution.py`
- `backend/app/modules/search/review_session.py`
- `backend/app/modules/search/review_query.py`
- `backend/app/modules/search/review_selection.py`
- `backend/app/modules/search/review_preparation.py`
- `backend/app/modules/search/review_admission.py`
- `backend/app/modules/documents/service.py`
- `backend/app/workers/fulltext.py`
- `backend/app/api/deps/services.py`
- `frontend/src/features/search/*`
- `frontend/src/api/generated/*`

## Follow-up Checks

- 如果实现后发现持久候选字段会明显膨胀，再评估把摘要或 source refs 做压缩/裁剪；不要退回 Redis-only。
- 如果真实搜索仍卡在 relevance `14 / 45`，另开独立 Worker/外部源运行态任务排查。
