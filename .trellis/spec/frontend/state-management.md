# Frontend State Management

## State Owners

Use `ref` and `computed` for page-local input and derived UI state. Use route
parameters and query strings for navigable, recoverable selection state. Use
TanStack Vue Query for API data. Pinia holds application-wide UI state such as
the authenticated user, access-token lifecycle, and authentication request
state.

References: `frontend/src/views/PlanReviewView.vue`,
`frontend/src/views/ResearchChatView.vue`, `frontend/src/stores/auth.ts`,
`frontend/src/api/hooks/research.ts`, `AGENT.md`.

## Authentication

The auth Pinia store owns token restoration, the current user, busy state, and
the user-facing error message. The router guard awaits `auth.restore()` before
entering protected routes, redirects unauthenticated users to login with a
`redirect` query parameter, and keeps logged-in users out of login and register
routes.

References: `frontend/src/stores/auth.ts`, `frontend/src/router/index.ts`,
`frontend/src/api/client.ts`.

## Server-State Recovery

Do not treat page memory as the completion record for a workflow. On refresh,
read the workspace and current search run through API queries, then derive the
route from server workflow state. `ResearchRunnerView` demonstrates this
recovery path.

References: `frontend/src/views/ResearchRunnerView.vue`,
`frontend/src/api/hooks/search.ts`, `AGENT.md`.

Do not duplicate server query results or persistent completion state in Pinia.
The product distinguishes the viewed candidate, Redis preparation selection,
PostgreSQL pending collection, and RAG-ready collection as separate states.

References: `AGENT.md`, `docs/08-development-environment.md`.
