# Frontend Type Safety

## API DTO Source

OpenAPI is the single DTO source. `pnpm api:generate` exports the backend
schema and generates `src/api/generated/schema.ts`; `pnpm api:check` regenerates
and fails if either generated artifact differs from the tracked version. CI
runs `api:check` before the frontend lint and test gates.

References: `frontend/package.json`, `scripts/export_openapi.py`,
`.github/workflows/quality.yml`, `frontend/src/api/generated/schema.ts`.

`src/api/types.ts` re-exports generated types and defines compatibility aliases
or documented application-level refinements. New API modules may import the
generated schema directly.

Reference: `frontend/src/api/types.ts`.

## HTTP Boundary

Call API modules through `apiFetch<T>`, which attaches the Bearer token when
present and turns non-success responses into `ApiError` with status and an
optional stable code. API modules provide typed request and response functions
instead of issuing `fetch` from views.

References: `frontend/src/api/client.ts`, `frontend/src/api/auth.ts`,
`frontend/src/api/workflow.ts`.

## Compiler And Linter

TypeScript is strict and rejects unused locals, unused parameters, and switch
fallthrough. ESLint combines the recommended JavaScript, TypeScript, and Vue
rules, ignores generated API files, and defers formatting to Prettier.

References: `frontend/tsconfig.json`, `frontend/eslint.config.mjs`,
`frontend/.prettierrc.json`.
