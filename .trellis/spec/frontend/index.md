# Frontend Development Guidelines

## Scope

`frontend/` is a Vue 3 and TypeScript application using Vue Router, Pinia,
TanStack Vue Query, Vite, Vitest, and Playwright. The development server binds
to `127.0.0.1:5173`; its API base URL is configured by `VITE_API_BASE_URL`.

References: `frontend/package.json`, `frontend/vite.config.ts`,
`frontend/.env.example`.

## Guides

| Guide | Use for |
| --- | --- |
| [Directory Structure](./directory-structure.md) | View, feature, API, and style placement |
| [Component Guidelines](./component-guidelines.md) | Vue composition, forms, and accessibility |
| [Hook Guidelines](./hook-guidelines.md) | Vue Query composables and cache updates |
| [State Management](./state-management.md) | Local, URL, Pinia, and server state |
| [Type Safety](./type-safety.md) | Generated API schema and strict TypeScript |
| [Quality Guidelines](./quality-guidelines.md) | Lint, format, unit, and browser checks |

## Local Commands

Run package scripts from `frontend/`:

```powershell
pnpm dev
pnpm api:check
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm test:e2e
```

References: `frontend/package.json`, `docs/08-development-environment.md`.
