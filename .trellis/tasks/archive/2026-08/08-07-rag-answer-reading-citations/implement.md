# 实施计划：RAG 回答阅读与引用交互

## Implementation Steps

1. 复读本任务规划、前端规范及当前 `ResearchChatView`、展示 helper、进度 composable、范围详情抽屉和测试；确认未提交的范围详情改动边界。
2. 实现不依赖 `v-html` 的受限 Markdown AST 与引用渲染 helper，并补充聚焦单元测试。
3. 在 `features/research/` 新增回答正文、运行摘要、引用来源/候选证据组件及配套样式；复用现有作者、定位和模式标签 helper。
4. 扩展展示 helper：实际引用/候选证据筛选、正确的审计标签和证据不足判定；为 Strict Research 增加真实 SSE 阶段历史。
5. 将 `ResearchChatView` 组合为新组件，管理引用检查焦点、局部提示和由 `paper_id` 驱动的范围详情选中状态。
6. 以最小方式扩展 `ResearchScopeDrawer` 的受控选中输入，保证直接打开、抽屉内切换、关闭和移动端返回列表均保持原有语义。
7. 更新现有研究会话 E2E fixture，增加完成 Fast/Strict 回答、正文引用检查、候选证据隔离、详情抽屉、证据不足和窄屏验收；为纯展示函数补充 Vitest。
8. 运行目标单测与 Playwright，再运行 format、lint、typecheck、完整前端单元测试和相关 E2E；视觉检查桌面与移动视口，并确认不会启动重复 API 或 Research Worker。

## Validation

从 `frontend/` 运行：

```powershell
corepack pnpm test:unit -- research-chat-presentation
corepack pnpm test:unit
corepack pnpm format:check
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm test:e2e -- research-chat-governance.spec.ts
```

再以 Playwright 在桌面和窄屏视口检查：受控 Markdown、引用滚动/焦点/高亮、来源默认收起、Strict 专属候选证据和轨迹、范围详情抽屉、长标题与无证据状态。

## Risk Controls

- 不使用 `v-html`，不让 Markdown 支持原始 HTML 或图片/嵌入内容。
- 不把 `run.evidences` 直接作为引用来源；所有默认来源均经过 `is_cited` 派生。
- 不用浏览器内存作为已完成研究的审计记录；阶段历史只说明当前 SSE 会话。
- 不覆盖现有未提交的工作区删除或研究范围详情改动；每次编辑前检查差异。
- 实现完成前不提交、推送、finish 或归档任务，除非用户另行明确要求。
