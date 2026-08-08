# Backend Development Guidelines

## Scope

`backend/` is a Python 3.12 service built with FastAPI, Pydantic, SQLAlchemy,
Alembic, and arq. The API process, four independently started arq Worker
entrypoints, and stateful development dependencies have separate start commands.

References: `backend/pyproject.toml`, `backend/app/main.py`,
`docs/08-development-environment.md`, and `AGENT.md`.

## Local Commands

Use the documented package commands rather than inventing a process wrapper:

```powershell
uv run --directory backend uvicorn app.main:app --reload
uv run --directory backend arq app.workers.workflow.WorkerSettings
uv run --directory backend arq app.workers.relevance.WorkerSettings
uv run --directory backend arq app.workers.ingestion.WorkerSettings
uv run --directory backend arq app.workers.research.WorkerSettings
```

Docker Compose starts PostgreSQL, Redis, etcd, MinIO, and Milvus only. It does
not start the API or Workers.

`app.workers.workflow` consumes intent-analysis, Provider search, and
candidate-fulltext task functions on `arq:queue:workflow`; `relevance`,
`ingestion`, and `research` each own a dedicated queue.

References: `docs/08-development-environment.md`,
`infra/compose/compose.dev.yml`.

## Guides

| Guide | Use for |
| --- | --- |
| [Directory Structure](./directory-structure.md) | API, Worker, domain, and adapter placement |
| [Database Guidelines](./database-guidelines.md) | PostgreSQL models, repositories, and Alembic |
| [Error Handling](./error-handling.md) | Domain errors and HTTP error mapping |
| [Logging Guidelines](./logging-guidelines.md) | Confirmed Python logging practices |
| [Quality Guidelines](./quality-guidelines.md) | Static checks, tests, and CI gates |
| [Candidate Review Persistence](./candidate-review-persistence.md) | PostgreSQL-owned candidate review facts and Redis responsibility boundary |
| [Candidate Relevance Execution](./candidate-relevance-execution.md) | Batch assessment, retry-subset, and persistent candidate contracts |
| [Research Run Lifecycle](./research-run-lifecycle.md) | ResearchRun status transitions, cancellation, Worker restart recovery, and SSE terminal events |
| [RAG Answer Citation And Verification](./rag-answer-citation-verification.md) | EvidenceSnapshot, EvidenceRef, verifier, composer, and user citation contracts |
