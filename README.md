# Learning AI System — Python backend

TASK002 provides a lean FastAPI modular-monolith skeleton, not a completed learning platform.
TASK003 adds explicit MySQL connection/session infrastructure and official Flyway delegation.
TASK004 adds minimal identity; TASK005 adds public categories, admin category writes and
owner/admin draft course CRUD. See [catalog/course scope and checks](docs/catalog-course.md).
Liveness does not connect to a database. Other business modules and external integrations
remain separate tasks.

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
- `/docs`, `/redoc`, and `/openapi.json` are disabled to avoid a parallel framework contract.
  The partial canonical API definition is [contracts/openapi.yaml](contracts/openapi.yaml).
- Responses have a generated UUID `X-Request-ID`. Caller-supplied IDs are ignored.
- Errors use `{code, message, details, request_id}`. Request validation returns HTTP 400,
  not FastAPI's default 422. Validation details identify fields/messages without echoing inputs;
  unexpected failures return a generic HTTP 500 message.

The default local URL is `http://127.0.0.1:8000`. Credentialed CORS is disabled unless exact
origins are configured; see [minimal identity setup](docs/identity.md). Do not expose the
development server as production.

## Checks

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Tests use FastAPI's in-process `TestClient` and a test-only router; no live services are used.
The app factory is `app.main.create_app(settings=None)` and accepts a `Settings` instance
for isolated tests or future composition.

## Database foundation (TASK003)

Set `DATABASE_URL` explicitly to a secret `mysql+pymysql://user:password@host:port/database`
URL (percent-encode reserved characters in credentials). There is no secret default. Only
`charset=utf8mb4` is accepted as an optional query; sessions use UTC. Supply secrets through
your environment or ignored `.env`, never commit them. SQLAlchemy engines connect lazily;
`session_scope(engine)` owns one commit/rollback transaction and always closes its session.
No `create_all`, Alembic, or startup migration runs.

`database/migrations/` contains canonical MySQL V1–V9 copied without byte changes. Applied SQL
is immutable. `.gitattributes` disables SQL line-ending conversion and offline tests pin raw
SHA-256 hashes. Flyway alone computes/validates its checksums and owns `flyway_schema_history`;
the Python code does not split SQL or write history. Do not repair/baseline a mismatched history.
In particular, an old local-only `V6__document_cleanup_jobs` is not this migration chain.

Install the **official Flyway CLI with MySQL support** separately and set `FLYWAY_EXECUTABLE`
(prefer an absolute executable path). No runner is bundled, downloaded, or auto-installed.
Pin an exact CLI release and record its `flyway -v` output in deployment/CI evidence after
qualification; no installed version or compatible release has been verified in this environment.
The CLI's Java/JDBC requirements are separate from Python's PyMySQL runtime dependencies.

From the repository directory, explicitly run:

```sh
uv run python -m app.core.db_cli info
uv run python -m app.core.db_cli migrate
uv run python -m app.core.db_cli validate
uv run python -m app.core.db_cli verify
```

The wrapper resolves migrations relative to its source, not the caller's directory, and supports
only `info`, `validate`, `migrate`. It disables clean, automatic baseline, out-of-order migration,
placeholder substitution, and schema creation. Supply a precreated authorized database and a
migration account with appropriate DDL permissions. Do not migrate the Java/source database.
`info` reports command success only: CLI stdout/stderr are deliberately suppressed because
JDBC errors may reveal secrets. Check runner availability and settings securely on failure;
never paste raw connection errors into public logs.

Run migrations once before web workers, not during startup. Use MySQL >=8.0.16 (enforced CHECK),
a pinned server version, and InnoDB; SQLite/H2/MariaDB are not compatibility proof. MySQL DDL
implicitly commits: failed migrations may leave partial changes. Do not automatically retry,
repair, or clean. For a V8 upgrade drain older uploaders first; keep staging tombstones until
no old writers can act. Separate runtime users from migration credentials where deployed.

`verify` is a bounded read-only foundation check: selected columns/types/nullability, IDs,
core unique/foreign keys, course price precision/scale, and exactly successful Flyway V1–V9
history for `users`, `auth_sessions`, `categories`, `courses`. It does not replace Flyway
checksum validation, prove all constraints/defaults/indexes, or validate all V1–V9 tables.
Extend this explicit check when later migrations/modules are adopted.

### Real MySQL tests (opt-in; acceptance pending)

Default tests require no database and mock the runner/schema inspector. To run integration
checks, explicitly set **both** `RUN_MYSQL_TESTS=1` and `TEST_DATABASE_URL` to a precreated,
empty, disposable MySQL database whose name starts `test_` or ends `_test`, plus a qualified
`FLYWAY_EXECUTABLE`, then run `uv run pytest -m mysql`. Test settings do not load `.env` or
fall back to `DATABASE_URL`. Name checks are guardrails, not proof a target is disposable:
obtain authorization first. Tests refuse a nonempty target and never drop/clean it afterward.

The opt-in suite checks fresh migration, Flyway validation, unchanged history after rerun,
foundation reflection, SQLAlchemy commit/rollback and email uniqueness. It intentionally
leaves fixtures/schema for inspection; use a newly provisioned target on each run. Upgrade
with existing data, generated-column/CHECK enforcement, failure recovery, and Java/Python
history parity need additional real-MySQL acceptance. No authorized database or official CLI
was available during TASK003 implementation; integration skips are not migration success.
