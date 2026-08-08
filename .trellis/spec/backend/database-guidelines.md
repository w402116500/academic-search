# Backend Database Guidelines

## Storage Boundaries

PostgreSQL owns durable business facts, including search-run candidate review
projections and candidate fulltext readiness states. Redis owns arq queues,
short-lived progress events, locks/leases, health checks, and disposable caches;
MinIO owns document objects; Milvus holds rebuildable vector indexes. Do not
move search candidates into persistent paper records before the documented
server-side admission boundary.

References: `AGENT.md`, `backend/app/infra/redis/search_session.py`,
`docs/08-development-environment.md`.

## SQLAlchemy Models

Use the shared `Base`, `UUIDPrimaryKeyMixin`, and `TimestampMixin` from
`backend/app/infra/db/base.py` when the entity matches their semantics. The
base supplies named metadata constraints, application-generated UUID primary
keys, and UTC audit timestamps. `backend/app/infra/db/models/__init__.py`
imports all owned models so Alembic can inspect `Base.metadata`.

Reference models: `backend/app/infra/db/models/user.py` and
`backend/app/infra/db/models/collection.py`.

## Sessions And Repositories

`get_db_session` yields one `AsyncSession` per request and intentionally does
not commit or roll back on behalf of a service. Repository methods own their
transaction boundary: simple writes in `workspaces.py` explicitly commit and
refresh, while account creation in `users.py` uses `async with
self._session.begin()` to make the uniqueness check and insert atomic.

References: `backend/app/infra/db/session.py`,
`backend/app/infra/db/repositories/workspaces.py`,
`backend/app/infra/db/repositories/users.py`.

```python
async with self._session.begin():
    existing = await self._session.scalar(
        select(User).where(func.lower(User.email) == command.email.lower())
    )
    if existing is not None:
        raise UserEmailConflictError
```

Source: `backend/app/infra/db/repositories/users.py`.

For user-owned records, query ownership at the persistence boundary. For
example, `SqlAlchemyWorkspaceRepository._owned_model` filters on both the
workspace ID and `owner_user_id` before a mutation.

Reference: `backend/app/infra/db/repositories/workspaces.py`.

## Migrations

Alembic runs against the async `DATABASE_URL`; `backend/alembic/env.py` filters
LangGraph checkpoint tables because they are not ORM-owned. Use the documented
commands from the repository root:

```powershell
uv run --directory backend alembic upgrade head
uv run --directory backend alembic current
uv run --directory backend alembic check
```

Create a new revision for an applied schema change rather than editing an
existing revision. Current revisions include both `upgrade()` and `downgrade()`
operations.

References: `backend/alembic/env.py`, `backend/alembic/versions/f41c8e7b2a06_align_workflow_unique_constraints.py`,
`backend/README.md`, `AGENT.md`.
