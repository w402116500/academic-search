# Backend Logging Guidelines

## Confirmed Pattern

Production modules use the Python standard library and bind a module logger
with `logging.getLogger(__name__)`.

References: `backend/app/workers/research.py`,
`backend/app/modules/search/relevance.py`,
`backend/app/modules/search/relevance_execution.py`.

When an exception is handled and execution cannot proceed, the observed
pattern is `logger.exception(...)` with a concise operational identifier such
as `research_run_id`, `run_id`, or a failure code. Lease-loss and retry-queue
conditions use `logger.error(...)` with the run ID.

References: `backend/app/workers/research.py`,
`backend/app/modules/search/relevance.py`,
`backend/app/modules/search/relevance_execution.py`.
