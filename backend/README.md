# Backend Development Environment

The backend package includes a minimal FastAPI application entry point at `app/main.py` and
an asynchronous SQLAlchemy/Alembic PostgreSQL foundation under `app/db/`. Business routes,
literature validation services, and workers are not implemented yet.

## Database Commands

Run these commands from the repository root after PostgreSQL has started:

```powershell
uv run --directory backend alembic upgrade head
uv run --directory backend alembic current
uv run --directory backend alembic check
```

The database URL comes from the repository-root `.env` file. Do not edit an existing Alembic
revision after it has been applied; create a new revision for every schema change.
