# Backend Quality Guidelines

## Required Repository Gates

The backend uses Ruff, Pyright, import-linter, and pytest. CI runs the
following commands from `backend/` after `uv sync --frozen --all-groups`:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports --config ../.importlinter
uv run python ../scripts/check_source_size.py
uv run pytest
```

References: `.github/workflows/quality.yml`, `backend/pyproject.toml`,
`.importlinter`, `scripts/check_source_size.py`.

## Test Selection

`pytest` discovers tests under `backend/tests`; asynchronous tests use the
project's session-scoped event loop. The autouse fixture removes local
literature-provider environment values from non-`live` tests. Marked live
tests are opt-in and may reach real services, so they are not part of the
ordinary local verification loop.

References: `backend/pyproject.toml`, `backend/tests/conftest.py`,
`backend/tests/integration/test_live_search_run.py`, `AGENT.md`.

For focused unit work, follow the existing fake-port style in
`test_authentication.py`. For an HTTP contract, override app dependencies and
exercise the app through `httpx.ASGITransport`, as in
`test_api_flow_contract.py`.

References: `backend/tests/unit/test_authentication.py`,
`backend/tests/integration/test_api_flow_contract.py`.

## Source Weight

`scripts/check_source_size.py` warns above 700 lines and fails above 1000
lines for maintained Python, TypeScript, Vue, and CSS sources, with explicitly
listed temporary exceptions. Treat a warning as an ownership review signal;
do not split a file mechanically.

References: `scripts/check_source_size.py`, `AGENT.md`.
