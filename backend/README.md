# Backend Development Environment

The backend package provides local account authentication, research-workspace, research-plan,
multi-source search, candidate review, fulltext admission, collection-build and research-session
APIs, an asynchronous SQLAlchemy/Alembic PostgreSQL foundation under `app/db/`, and intent-analysis,
search, fulltext, RAG-ingestion and research workers.

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
- `POST /collections`, `GET /collections?q=&cursor=&limit=`, `GET /collections/{id}`
- `PATCH /collections/{id}`, `POST /collections/{id}/archive`, and
  `POST /collections/{id}/restore`
- `POST /collections/research` creates a workspace and starts structured intent analysis
- `GET /collections/{id}/plan`, `POST /collections/{id}/plan/regenerate`, and
  `POST /collections/{id}/plan/confirm`
- `POST /collections/{id}/search-runs`, `GET /collections/{id}/search-runs/current`, and
  `GET /collections/{id}/search-runs/{run_id}/candidates?limit=&cursor=&query=&filter=`
- `GET /collections/{id}/search-runs/{run_id}/candidates/{candidate_id}` reads one candidate
  independently of its current result page.
- `PATCH` / `DELETE .../candidate-selection` update or clear the Redis-backed preparation list;
  `POST .../candidate-selection/prepare` dispatches per-candidate metadata/fulltext preparation;
  `POST .../candidate-selection/admission` admits only eligible prepared items to the persistent
  awaiting-confirmation collection.
- `GET /collections/{id}/search-runs/{run_id}/events` streams resumable progress events, and
  `POST /collections/{id}/search-runs/{run_id}/retry` creates a new attempt after failure.
- `POST /collections/{id}/search-runs/{run_id}/candidates/{candidate_id}/fulltext` starts a
  server-side open-access fulltext task; its `GET` counterpart polls the Redis-backed status, and
  `/fulltext/retry` creates a new retryable attempt.
- `POST .../fulltext/admission` only accepts an `available` server-side result and creates a
  `pending` ingestion run. It never accepts a client-supplied paper URL, metadata or object key.
- `GET /collections/{id}/documents` returns active documents, latest ingestion status and the
  count currently usable for RAG. `POST /collections/{id}/build` changes all pending documents to
  `queued` and dispatches the ingestion jobs. Failed runs are retried through
  `POST /collections/{id}/ingestion-runs/{ingestion_run_id}/retry`; a pending document may be
  safely archived through `DELETE /collections/{id}/documents/{document_id}`.

Collection APIs require the `Authorization: Bearer <access_token>` header. `POST /collections`
creates a manually named empty workspace; homepage-style submission uses
`POST /collections/research`, which stores the raw requirement and queues intent analysis. A
confirmed plan starts a search explicitly through `POST /collections/{id}/search-runs`; keeping
confirmation and the potentially expensive multi-source request as separate actions makes the
user's intent auditable.

The workspace switcher should call `GET /collections?q=<keyword>&cursor=<next_cursor>&limit=20`.
The response is `{items, next_cursor}`; `next_cursor` is opaque and should be sent back unchanged.
It matches workspace names and the server-provided workflow-stage text. On page refresh, fetch the
workspace, current plan, current search run and candidates again instead of replaying local UI
animations. A missing Redis candidate session is returned as HTTP 410 and the run is marked expired.

## Verification Commands

Run the offline regression and static checks from `backend/`:

```powershell
uv run pytest tests -q
uv run ruff check app tests
uv run ruff format --check app tests
uv run pyright
```

The API state-recovery acceptance test uses temporary PostgreSQL and Redis records. It does not
call external models or providers:

```powershell
$env:RUN_LIVE_API_STATE_RECOVERY_TESTS = "1"
uv run pytest tests/integration/test_live_api_state_recovery.py -m live -s
```

The external embedding smoke test is separately gated by `RUN_LIVE_EMBEDDING_TESTS=1`.

The complete candidate-review acceptance test uses a real arXiv open PDF, the workflow Worker,
Redis, MinIO and the batch-admission service. It creates and precisely cleans temporary records.
On the current local network arXiv is more reliable through the explicit fulltext proxy mode; this
is only a test-process override and does not require changing `.env`:

```powershell
$env:RUN_LIVE_CANDIDATE_REVIEW_E2E_TESTS = "1"
$env:FULLTEXT_NETWORK_MODE = "proxy"
$env:LITERATURE_PROXY_URL = "http://127.0.0.1:7897"
$env:FULLTEXT_DOWNLOAD_TIMEOUT_SECONDS = "45"
uv run pytest tests/integration/test_live_candidate_review_e2e.py -m live -s
```

The candidate-relevance acceptance test calls the configured DeepSeek/OpenAI-compatible chat model with
one controlled unified candidate. It validates that every user-facing evidence quote can be found in the
candidate title or abstract, and it does not create PostgreSQL, Redis, MinIO, or Milvus data:

```powershell
$env:RUN_LIVE_CANDIDATE_RELEVANCE_TESTS = "1"
uv run pytest tests/integration/test_live_candidate_relevance.py -m live -s
```

## Workers

Run the workflow, relevance, and RAG ingestion workers in separate terminals:

```powershell
uv run --directory backend arq app.workers.workflow.WorkerSettings
uv run --directory backend arq app.workers.relevance.WorkerSettings
uv run --directory backend arq app.workers.ingestion.WorkerSettings
```

The workflow worker reads the root `.env` and consumes intent-analysis, Provider search and candidate-fulltext jobs from
the dedicated `arq:queue:workflow` queue. The relevance worker consumes complete-candidate semantic analysis from
`arq:queue:relevance`; it has no total job timeout and renews its ARQ and Redis leases while a model stream remains active. It
uses `SEARCH_HTTP_TIMEOUT_SECONDS`, `SEARCH_MAX_CONCURRENT_PROVIDERS`,
`SEARCH_SESSION_TTL_SECONDS`, and `SEARCH_CITATION_ENRICHMENT_LIMIT` for the search pipeline.
The ingestion worker remains separate and only receives a document from `arq:queue:ingestion` after the user
confirms the collection build. The workflow worker uses `WORKFLOW_CHAT_PROVIDER` and the
selected provider's credentials. The default `deepseek` mode uses `DEEPSEEK_API_KEY`,
`DEEPSEEK_BASE_URL`, and `DEEPSEEK_CHAT_MODEL`; `openai_compatible` mode uses the corresponding
`OPENAI_*` chat settings. `WORKFLOW_INTENT_TIMEOUT_SECONDS` controls research-plan analysis. After
normalization and deterministic triage, the workflow worker publishes the complete eligible candidate collection and
hands it to the relevance worker in one shared context. It never blocks relevance assessment merely because of candidate count
or silently splits the collection. `WORKFLOW_RELEVANCE_STREAM_IDLE_TIMEOUT_SECONDS`,
`WORKFLOW_RELEVANCE_OUTPUT_TOKENS_PER_CANDIDATE`, and
`WORKFLOW_RELEVANCE_VERIFICATION_OUTPUT_TOKENS_PER_CANDIDATE` control the two complete-collection
model calls; the timeout is only the period without any stream activity, not a wall-clock limit. Candidates without an
abstract remain deterministic `insufficient_information`. The worker validates returned evidence against the same candidate
metadata; a retryable failure is retried as a complete current-candidate collection, without hiding other candidates. The
workflow worker validates a JSON research-plan draft before making it available
for user confirmation.
