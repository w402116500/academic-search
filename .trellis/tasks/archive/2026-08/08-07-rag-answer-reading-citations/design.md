# 技术设计：RAG 回答阅读与引用交互

## Boundary And Ownership

本任务仅改动 `frontend/`。后端已经提供完成本功能所需的 `ResearchRun`、`ResearchEvidence` 和研究范围文献字段，因此不改 API、OpenAPI 或持久化模型。

`ResearchChatView.vue` 保持路由、查询和跨组件协作职责：持有当前高亮引用、局部提示和研究范围抽屉的受控选中文献。回答 Markdown、运行摘要和证据列表拆到 `features/research/` 下的特性组件/纯函数，避免继续扩大路由视图。

## Presentation Data Model

在 `research-chat-presentation.ts` 集中派生只供展示的数据：

- `citedEvidences(run)`：筛选 `is_cited=true`，按 `display_index` 排序。
- `candidateEvidences(run)`：只在 Strict Research 中筛选未引用的证据。
- `citationAuditLabel(run)`：按实际引用数计数；快速问答仅允许“引用已检查”，严格研究才允许“引用与主张已核验”。
- `isEvidenceInsufficientRun(run)`：仅根据持久运行的终态与实际引用数识别澄清/证据不足边界，不能从回答自然语言猜测。

该层是正文引用、来源卡片和审计摘要的唯一筛选入口，防止任何视图重新以 `run.evidences` 作为默认来源列表。

## Safe Markdown And Citation Rendering

新增受限 Markdown AST 解析器，只识别 PRD 列出的结构，并由 Vue 以文本节点渲染。原始 HTML、图片、媒体与嵌入内容不会成为 DOM 元素；模型文本永远不作为 Vue 模板执行，也不使用 `v-html`。

回答中仅匹配已存在于 `citedEvidences` 的 `[display_index]`。这些标记由受控渲染器替换为带 `data-citation-index` 的原生按钮，组件通过单一点击/键盘事件向页面发出 `inspect-citation`。不存在于当前证据快照的 `[n]` 保持普通文本，不能创造虚假来源入口。

为避免语法分支影响安全边界，解析和引用标记替换在纯 helper 中集中，最少以单元测试覆盖标题、表格、引用、恶意 HTML、图片/嵌入和有效/无效引用索引。

## In-Place Citation Inspection

证据列表使用原生 `<details>`，默认关闭。`ResearchChatView` 在收到 `inspect-citation(index)` 后：

1. 将来源列表设为展开；
2. `nextTick` 后定位以运行 ID 与证据 ID 组成的稳定 DOM ID；
3. 调用 `scrollIntoView`，添加短暂高亮状态并以 `tabindex=-1` 聚焦卡片；
4. 不修改路由、不打开外链、不改变对话选择。

来源论文标题是独立按钮。页面用 `evidence.paper_id` 匹配已加载的范围文献的 `paper_id`，成功时将选中文献 ID 传给 `ResearchScopeDrawer` 并打开它。抽屉增加可选的受控选中输入，仍保留用户在抽屉内自行切换文献的行为；未匹配则显示局部提示。

## Mode And Progress Presentation

- Fast RAG：状态、模式标签、紧凑审计摘要、引用来源。无候选证据和阶段轨迹。
- Strict Research：在上述信息外，可展开显示本浏览器会话收到的去重 SSE 阶段轨迹，以及候选证据。

`useResearchProgress` 新增仅供实时展示的阶段历史，在 `reset`、停止和切换对话时清空。完成或刷新后不把内存中的历史伪装为持久事实，仍以当前 `ResearchRun` 的审计摘要显示完成信息。

当 `isEvidenceInsufficientRun` 为真时，以独立状态面板替代普通回答来源区；该面板提供深度研究模式的重试入口，并不将该状态视为异常错误。

## Compatibility, Rollout, And Rollback

- 历史回答没有匹配运行时，仍以同一安全 Markdown 组件展示，但没有可交互引用列表。
- 历史运行缺少 `display_index`、`paper_id` 或范围映射时不崩溃：忽略无效引用控制并展示局部不可用提示。
- 仅新增前端依赖及组件，不改变服务端数据。回滚该前端变更即可恢复现有行为，不需数据库迁移或数据修复。
- 与当前未提交的 `ResearchScopeDrawer` 文献详情工作并行：只在已存在的受控状态上做最小扩展，不重写其布局、元数据或移动端行为。
