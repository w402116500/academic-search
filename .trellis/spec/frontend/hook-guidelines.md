# Frontend Hook Guidelines

## API Hooks

Server-state composables live in `src/api/hooks/` and wrap TanStack Vue Query.
They accept `MaybeRefOrGetter` inputs, derive a reactive `queryKey` with
`computed` and `toValue`, and gate requests with `enabled` where an identifier
may be absent.

References: `frontend/src/api/hooks/search.ts`,
`frontend/src/api/hooks/research.ts`.

```typescript
return useQuery({
  queryKey: computed(() => queryKeys.search.run(toValue(workspaceId))),
  queryFn: () => getCurrentSearchRun(toValue(workspaceId)),
});
```

Source: `frontend/src/api/hooks/search.ts`.

## Query Keys

Use the canonical keys from `api/hooks/query-keys.ts`. Search candidate keys
include workspace ID, run ID, candidate ID when applicable, and pagination
inputs where the page response depends on them.

References: `frontend/src/api/hooks/query-keys.ts`,
`frontend/src/api/hooks/search.ts`.

## Mutations

Keep mutation functions in the hook that owns the relevant query family. On a
successful mutation, invalidate or refresh the affected canonical queries.
Candidate-review mutations refresh candidate pages, verification pages, and the
current run together; collection mutations invalidate documents.

References: `frontend/src/api/hooks/search.ts`,
`frontend/src/api/hooks/research.ts`.

## Feature Composables

Progress streaming and polling stay with their domain feature rather than in
the generic API hook directory. Search and research use separate feature
composables for their respective recovery paths.

References: `frontend/src/features/search/use-search-progress.ts`,
`frontend/src/features/search/use-review-polling.ts`,
`frontend/src/features/research/use-research-progress.ts`, `AGENT.md`.
