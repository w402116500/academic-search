# Frontend Quality Guidelines

## Required Repository Gates

CI installs the locked pnpm dependencies, checks generated OpenAPI artifacts,
then runs format, lint, type, unit-test, and Playwright gates. Browser tests
install Chromium in CI.

When CI enables `actions/setup-node` pnpm caching, install pnpm first with
`pnpm/action-setup` using `frontend/package.json`; otherwise setup-node may
try to locate pnpm before Corepack has exposed it.

References: `.github/workflows/quality.yml`, `frontend/package.json`.

Use the matching local scripts:

```powershell
pnpm api:check
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm test:e2e
```

References: `frontend/package.json`, `docs/08-development-environment.md`.

## Tests

Vitest runs TypeScript unit tests under `tests/unit/` in jsdom. Test pure
feature or routing behavior with direct imported functions, as in
`workspace-route.test.ts` and `research-scope.test.ts`.

References: `frontend/vitest.config.ts`,
`frontend/tests/unit/workspace-route.test.ts`,
`frontend/tests/unit/research-scope.test.ts`.

Playwright runs browser tests from `tests/e2e/` against Vite at a local
127.0.0.1 URL, retains a trace on failure, and reuses an existing local server
outside CI. E2E specs assert user-visible semantics with roles, labels, and
attribute checks.

References: `frontend/playwright.config.ts`,
`frontend/tests/e2e/auth-flow.spec.ts`.

When an E2E spec mocks API traffic, intercept `**/api/v1/**` or a URL derived
from the same frontend API base instead of hard-coding port `8000`. Local
acceptance often runs the real API on `8001`, and a fixed-port mock can silently
fall through to the live backend.

## Verification Scope

Choose tests according to the changed behavior. Do not add live backend,
provider, Docker, or unrelated browser checks for documentation-only or
local pure-logic work.

Reference: `AGENT.md`.
