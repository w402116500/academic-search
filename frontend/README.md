# Frontend Development

Vue 3 前端承接同一条研究工作流：输入研究要求、确认计划、观察多源检索、分页审核候选、准备全文、确认研究集合，再进入带证据的研究对话。

## Runtime and commands

项目锁定 Node.js `20.19.6` 与 pnpm `10.34.5`。在 Windows 本地环境中，可显式使用项目已验证的运行时：

```powershell
E:\nodejs\corepack.cmd pnpm install --frozen-lockfile
E:\nodejs\corepack.cmd pnpm dev
```

运行 API 和 Worker 的完整说明见 [`../docs/08-development-environment.md`](../docs/08-development-environment.md)。

## Candidate review interaction

候选结果页采用服务端游标分页。点击表格行只改变右侧检查器的“正在查看”焦点；复选框独立维护当前搜索运行的 Redis 准备清单，因此跨页、筛选和刷新不会丢失选择。

准备清单不是收藏，也不是待确认集合：它只用于批量准备题录和开放获取全文。只有 DOI、题录和已验证全文都通过后，批量准入接口才会把文献写入 PostgreSQL 待确认集合；用户确认构建后，入库 Worker 才开始解析、嵌入和向量索引。

## Verification

在 `frontend/` 目录执行：

```powershell
E:\nodejs\corepack.cmd pnpm format:check
E:\nodejs\corepack.cmd pnpm lint
E:\nodejs\corepack.cmd pnpm typecheck
E:\nodejs\corepack.cmd pnpm test:unit
E:\nodejs\corepack.cmd pnpm test:e2e
E:\nodejs\corepack.cmd pnpm build
```

`tests/e2e/candidate-review.spec.ts` 覆盖跨页选择、刷新恢复、只看已选、批量核验、批量准入和待确认集合计数同步。
