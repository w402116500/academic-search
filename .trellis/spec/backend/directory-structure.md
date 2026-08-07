# Backend Directory Structure

## Application Boundary

`backend/app/main.py` creates the FastAPI app, loads environment configuration,
adds CORS, includes the versioned router, and exposes only root-level system
endpoints such as `/healthz`. Business endpoints are registered below
`/api/v1` in `backend/app/api/routers/router.py`.

References: `backend/app/main.py`, `backend/app/api/routers/router.py`.

```python
router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
```

Source: `backend/app/api/routers/router.py`.

## Layer Placement

| Path | Ownership |
| --- | --- |
| `backend/app/api/routers/` | HTTP/SSE parameters, authentication dependencies, response models, and domain-error-to-HTTP mapping |
| `backend/app/api/deps/` | Named request-scoped dependency composition |
| `backend/app/modules/auth/`, `backend/app/modules/research/`, `backend/app/modules/search/` | Domain contracts, ports, services, state, and use cases |
| `backend/app/infra/` | SQLAlchemy, Redis, arq, object storage, and other concrete adapters |
| `backend/app/workers/` | Job payload validation, Worker composition, retry boundaries, and use-case invocation |
| `backend/alembic/` | PostgreSQL schema revisions |
| `backend/tests/unit/` | Isolated service, contract, provider, and adapter behavior |
| `backend/tests/integration/` | API and opt-in live infrastructure or provider flows |

References: `backend/app/api/deps/services.py`, `backend/app/modules/auth/service.py`,
`backend/app/infra/db/repositories/users.py`, `backend/app/workers/research.py`,
`backend/alembic/env.py`, `backend/tests/`.

## Retired Paths

Do not recreate the pre-refactor source directories `backend/app/db`,
`backend/app/modules/collections`, `backend/app/modules/fulltext`,
`backend/app/modules/ingestion`, or `backend/app/modules/workflow`. They were
split into the current owners: `backend/app/infra/db`,
`backend/app/modules/research`, `backend/app/modules/documents`,
`backend/app/modules/rag/ingestion`, `backend/app/modules/search`, and
`backend/app/modules/agents`.

## Dependency Direction

Business modules do not import API routers, Workers, or infrastructure
implementations. Routers receive composed services via `Depends`; the explicit
composition exceptions are localized to `app/api/deps/services.py`.

References: `.importlinter`, `backend/app/api/deps/services.py`, `AGENT.md`.

## API Pattern

Routers declare a domain-specific `APIRouter` prefix and tags, accept Pydantic
request contracts, inject the current user and service, and pass the current
user ID to the service for owned-resource operations. `auth.py` and
`candidate_citations.py` are representative routes.

References: `backend/app/api/routers/auth.py`,
`backend/app/api/routers/candidate_citations.py`,
`backend/app/api/deps/auth.py`.
