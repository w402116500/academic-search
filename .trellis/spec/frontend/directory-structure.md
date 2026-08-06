# Frontend Directory Structure

## Application Entry

`frontend/src/main.ts` creates the app, installs Pinia, Vue Router, and
TanStack Vue Query, then imports the global and feature stylesheet set.
`App.vue` is intentionally a `RouterView` shell.

References: `frontend/src/main.ts`, `frontend/src/App.vue`.

## Placement

| Path                                                                                                                                   | Ownership                                                              |
| -------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `frontend/src/views/`                                                                                                                  | Route-level composition and route parameters                           |
| `frontend/src/features/auth/`, `frontend/src/features/research/`, `frontend/src/features/search/`, `frontend/src/features/literature/` | Domain UI, presentation helpers, CSS, and feature-specific composables |
| `frontend/src/api/`                                                                                                                    | HTTP modules, API client, generated schema, and compatibility types    |
| `frontend/src/api/hooks/`                                                                                                              | Vue Query hooks and canonical query keys                               |
| `frontend/src/stores/`                                                                                                                 | Pinia stores for UI-owned global state                                 |
| `frontend/src/router/`                                                                                                                 | Route definitions, guards, and workflow-stage routing                  |
| `frontend/src/components/`                                                                                                             | Reusable cross-feature UI components                                   |
| `frontend/src/assets/styles/`                                                                                                          | Shared reset, component, overlay, feedback, and responsive CSS         |

References: `frontend/src/router/index.ts`, `frontend/src/api/hooks/query-keys.ts`,
`frontend/src/stores/auth.ts`, `frontend/src/features/search/`,
`frontend/src/assets/styles/`, `AGENT.md`.

## Naming

Route-level Vue files normally use `*View.vue`; routed layouts that host child
routes use a descriptive PascalCase `*Frame.vue`, such as `WorkspaceFrame.vue`.
Reusable Vue files use PascalCase; feature helpers and Vue Query modules use
kebab-case file names; composable exports use a `use` prefix.

References: `frontend/src/views/AuthView.vue`,
`frontend/src/views/WorkspaceFrame.vue`,
`frontend/src/features/search/CandidateReviewTable.vue`,
`frontend/src/features/search/use-review-polling.ts`,
`frontend/src/api/hooks/search.ts`.
