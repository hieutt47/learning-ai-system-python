# Learning AI System — Python backend

TASK002 provides a lean FastAPI modular-monolith skeleton, not a completed learning platform.
It contains application configuration, liveness, request IDs, shared error handling, and empty
`identity`, `catalog`, and `course` module packages. There is no database, authentication,
business API, frontend, or external-service integration yet.

## Local setup

Install [uv](https://docs.astral.sh/uv/) and use Python 3.12. From this repository directory:

```sh
uv sync --locked
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`uv sync` creates the local `.venv`; uv can obtain Python 3.12 if it is not available.
`uv.lock` records resolved dependencies, including development tools. No global package
installation or running service is required for the tests.

Configuration defaults work without an `.env` file. For optional overrides, copy
`.env.example` to `.env` and edit `APP_NAME` or `APP_ENVIRONMENT`
(`development`, `test`, or `production`). `.env` and secret files are ignored by Git.
Debug tracebacks are disabled. The example contains no credentials.

## Routes and errors

- `GET /health` returns `{"status":"ok"}`; this is liveness only, not dependency readiness.
- `/docs`, `/redoc`, and `/openapi.json` are FastAPI's generated documentation for this
  skeleton, not an approved shared business API contract.
- Responses have a generated UUID `X-Request-ID`. Caller-supplied IDs are ignored.
- Errors use `{code, message, details, request_id}`. Request validation returns HTTP 400,
  not FastAPI's default 422. Validation details identify fields/types without echoing inputs;
  unexpected failures return a generic HTTP 500 message.

The default local URL is `http://127.0.0.1:8000`. CORS is not configured; frontend origins
and integration remain future work. Do not expose the development server as production.

## Checks

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Tests use FastAPI's in-process `TestClient` and a test-only router; no live services are used.
The app factory is `app.main.create_app(settings=None)` and accepts a `Settings` instance
for isolated tests or future composition.
