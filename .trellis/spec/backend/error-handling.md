# Backend Error Handling

## Input And Domain Boundaries

Use Pydantic request and response contracts at the API boundary. The auth
contracts validate and normalize email, enforce password and display-name
constraints, and expose only safe response fields.

Reference: `backend/app/modules/auth/contracts.py`.

Represent expected domain failures with a stable code and message, then map
them in the router. `AuthError` carries `AuthErrorCode`; `auth.py` translates
it to an HTTP status and `{ "code", "message" }` detail. Candidate citation
and search-run errors follow the same router-local mapping shape.

References: `backend/app/modules/auth/contracts.py`,
`backend/app/api/routers/auth.py`,
`backend/app/api/routers/candidate_citations.py`.

```python
raise HTTPException(
    status_code=status_code,
    detail={"code": error.code, "message": str(error)},
)
```

Source: `backend/app/api/routers/auth.py`.

## Authentication Failures

Protected routes use `get_current_user`. It accepts an HTTP Bearer credential,
validates the JWT, then confirms that the account remains active. Missing,
invalid, expired, or inactive credentials receive the same 401 error with a
`WWW-Authenticate: Bearer` header; unavailable signing configuration is a 503.

References: `backend/app/api/deps/auth.py`, `backend/app/core/security.py`.

## Client Error Contract

The frontend API client reads `{ detail: { code, message } }` errors and
exposes them as `ApiError`; FastAPI validation arrays receive a separate
user-facing message. Keep API error changes compatible with this consumer.

Reference: `frontend/src/api/client.ts`.

## Test Shape

Use an in-memory port replacement for domain-service tests, and dependency
overrides with `httpx.ASGITransport` for API contract tests. Authentication
tests assert that registration fails before writing when signing configuration
is invalid, while API-flow tests assert the HTTP sequence and recovery fields.

References: `backend/tests/unit/test_authentication.py`,
`backend/tests/integration/test_api_flow_contract.py`.
