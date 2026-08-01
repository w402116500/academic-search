# Backend Development Environment

The backend package provides local account authentication, research-workspace, research-plan and
multi-source search APIs, an asynchronous SQLAlchemy/Alembic PostgreSQL foundation under
`app/db/`, and intent-analysis, search and RAG-ingestion workers. Research-agent APIs are not
implemented yet.

## Database Commands

Run these commands from the repository root after PostgreSQL has started:

```powershell
uv run --directory backend alembic upgrade head
uv run --directory backend alembic current
uv run --directory backend alembic check
```

The database URL comes from the repository-root `.env` file. Do not edit an existing Alembic
revision after it has been applied; create a new revision for every schema change.

## Authentication And Workspaces

Set a random `AUTH_JWT_SECRET_KEY` with at least 32 characters in the repository-root `.env`.
The versioned APIs are available under `/api/v1`:

- `POST /auth/register`, `POST /auth/login`, and `GET /auth/me`
- `POST /collections`, `GET /collections`, `GET /collections/{id}`
- `PATCH /collections/{id}`, `POST /collections/{id}/archive`, and
  `POST /collections/{id}/restore`
- `POST /collections/research` creates a workspace and starts structured intent analysis
- `GET /collections/{id}/plan`, `POST /collections/{id}/plan/regenerate`, and
  `POST /collections/{id}/plan/confirm`
- `POST /collections/{id}/search-runs`, `GET /collections/{id}/search-runs/current`, and
  `GET /collections/{id}/search-runs/{run_id}/candidates`
- `GET /collections/{id}/search-runs/{run_id}/events` streams resumable progress events, and
  `POST /collections/{id}/search-runs/{run_id}/retry` creates a new attempt after failure.

Collection APIs require the `Authorization: Bearer <access_token>` header. `POST /collections`
creates a manually named empty workspace; homepage-style submission uses
`POST /collections/research`, which stores the raw requirement and queues intent analysis. A
confirmed plan starts a search explicitly through `POST /collections/{id}/search-runs`; keeping
confirmation and the potentially expensive multi-source request as separate actions makes the
user's intent auditable.

## Workers

Run the intent-analysis worker in one terminal and the RAG ingestion worker in another:

```powershell
uv run --directory backend arq app.workers.workflow.WorkerSettings
uv run --directory backend arq app.workers.ingestion.WorkerSettings
```

The workflow worker reads the root `.env` and consumes both intent-analysis and search jobs. It
uses `SEARCH_HTTP_TIMEOUT_SECONDS`, `SEARCH_MAX_CONCURRENT_PROVIDERS`,
`SEARCH_SESSION_TTL_SECONDS`, and `SEARCH_CITATION_ENRICHMENT_LIMIT` for the search pipeline.
The ingestion worker remains separate. The workflow worker uses `WORKFLOW_CHAT_PROVIDER` and the
selected provider's credentials. The default `deepseek` mode uses `DEEPSEEK_API_KEY`,
`DEEPSEEK_BASE_URL`, and `DEEPSEEK_CHAT_MODEL`; `openai_compatible` mode uses the corresponding
`OPENAI_*` chat settings. `WORKFLOW_INTENT_TIMEOUT_SECONDS` controls the request timeout. The
worker validates a JSON research-plan draft before making it available for user confirmation.
