# Frontend Component Guidelines

## Vue Composition

Vue components use `<script setup lang="ts">` with imported feature helpers,
route composition APIs, `ref` for local mutable state, and `computed` for
derived state. Page-level views coordinate feature hooks rather than keeping
API query implementation inline.

References: `frontend/src/views/AuthView.vue`,
`frontend/src/views/PlanReviewView.vue`,
`frontend/src/views/ResearchChatView.vue`.

## Forms And Validation

Use native form submission with `@submit.prevent`, bind inputs with `v-model`,
and keep form-specific errors local to the view. The authentication form uses
native `type`, `required`, `autocomplete`, and `minlength` constraints; its
submission error is exposed with `role="alert"`.

References: `frontend/src/views/AuthView.vue`,
`frontend/tests/e2e/auth-flow.spec.ts`.

When a page has domain validation that must be independently tested, place it
in a typed feature helper. `buildResearchScope` converts view input to the API
scope contract and is covered by a focused Vitest suite.

References: `frontend/src/features/research/scope.ts`,
`frontend/tests/unit/research-scope.test.ts`.

## Accessibility Evidence

Current interactive views use labelled controls and explicit ARIA roles or
states for non-native controls, live task status, dialogs, and alert messages.
Browser tests select authentication fields by their accessible labels and
buttons by role and name.

References: `frontend/src/views/AuthView.vue`,
`frontend/src/views/PlanReviewView.vue`,
`frontend/src/views/VerificationTaskView.vue`,
`frontend/tests/e2e/auth-flow.spec.ts`.

## Styling

Stylesheets are imported from the application entrypoint: `src/styles.css` is
the global base, reusable shared styles live under `src/assets/styles/`, and
page or feature styles live beside their feature. The auth view is styled by
`features/auth/auth.css` imported in `main.ts`.

References: `frontend/src/main.ts`, `frontend/src/features/auth/auth.css`.

## 受限 Markdown 与研究引用

研究回答属于不可信模型文本。需要可读格式时，在 feature 内先解析为受限 AST，
再由 Vue 文本节点渲染；不得使用 `v-html`、运行时模板编译，也不得把原始 HTML、
图片、脚本或嵌入媒体转换为 DOM。当前实现见
`frontend/src/features/research/answer-markdown.ts` 与
`frontend/src/features/research/ResearchAnswerMarkdown.vue`。

正文中的 `[n]` 只有在 `citedEvidences(run)` 返回的 `display_index` 中存在时，才可
渲染为带可访问名称的引用按钮；未知索引保持普通文本。点击引用由页面视图负责展开
引用来源、滚动并聚焦对应证据卡片，不能改变当前对话路由，也不能从模型文本生成外链。

```typescript
const indexes = citedEvidenceIndexes(run);
const blocks = parseAnswerMarkdown(answer, indexes);
```

变更该边界时，单元测试必须覆盖恶意 HTML 只作为文本、有效与无效引用索引；浏览器
测试必须覆盖引用卡片的展开、焦点和 Fast/Strict 来源隔离。

`ResearchEvidenceResponse.authors` 是开放的作者字典列表。展示时使用
`evidenceAuthors` 规范化 `name`、`literal` 或 `given`/`family`，不要只假设 `name`
存在；否则已经保存的作者会被错误显示为待补全。
