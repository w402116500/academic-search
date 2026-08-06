# Frontend Development Guidelines

## Scope

`frontend/` is a Vue 3 and TypeScript application using Vue Router, Pinia,
TanStack Vue Query, Vite, Vitest, and Playwright. The development server binds
to `127.0.0.1:5173`; its API base URL is configured by `VITE_API_BASE_URL`.

References: `frontend/package.json`, `frontend/vite.config.ts`,
`frontend/.env.example`.

## Guides

| Guide                                             | Use for                                    |
| ------------------------------------------------- | ------------------------------------------ |
| [Directory Structure](./directory-structure.md)   | View, feature, API, and style placement    |
| [Component Guidelines](./component-guidelines.md) | Vue composition, forms, and accessibility  |
| [Hook Guidelines](./hook-guidelines.md)           | Vue Query composables and cache updates    |
| [State Management](./state-management.md)         | Local, URL, Pinia, and server state        |
| [Type Safety](./type-safety.md)                   | Generated API schema and strict TypeScript |
| [Quality Guidelines](./quality-guidelines.md)     | Lint, format, unit, and browser checks     |

## Local Commands

Use Node `20.19.6` from `.node-version` (the package engines allow Node
`>=20.19.0 <21`) and the Corepack-managed pnpm `10.34.5` declared in
`package.json`. Run package scripts from `frontend/`; when the ambient `pnpm`
does not honor that package-manager version, prefix the same command with
`corepack`.

```powershell
pnpm dev
pnpm api:check
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm test:e2e
```

For example, use `corepack pnpm lint` when the shell exposes an incompatible
global pnpm version.

References: `frontend/package.json`, `docs/08-development-environment.md`.
