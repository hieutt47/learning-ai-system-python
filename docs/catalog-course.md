# Catalog and draft course management (TASK005)

Implemented routes share the canonical `contracts/openapi.yaml` and HTTP error envelope.

| Method | Route | Access |
| --- | --- | --- |
| GET | `/api/v1/categories` | Public |
| POST | `/api/v1/categories` | Admin |
| PATCH, DELETE | `/api/v1/categories/{category_id}` | Admin |
| POST | `/api/v1/courses` | Teacher/admin; actor becomes owner |
| GET | `/api/v1/teaching/courses` | Teacher's courses; admin sees all |
| GET | `/api/v1/teaching/courses/{course_id}` | Owner/admin |
| PATCH, DELETE | `/api/v1/courses/{course_id}` | Owner/admin; draft only |

Lists accept one-based `page` and `page_size` (default 20, maximum 100), returning
`{items,page,page_size,total}` ordered by creation time then ID descending. IDs are positive
signed BIGINT decimal strings. Prices are nonnegative decimal strings with up to twelve integer
and two fractional digits; numbers, exponent notation and nonfinite values are rejected.
Responses format prices with two decimal places and timestamps as UTC `Z` values.

Category create requires nonblank trimmed name (maximum 120); description is optional.
Course create requires trimmed nonblank title (maximum 200), description, category ID and price.
Unknown fields are rejected, including caller-selected owner/publication/archive/thumbnail data.
PATCH permits empty objects and treats explicit null as unchanged, not field clearing.
Course create returns 201 and `Location: /api/v1/teaching/courses/{id}`; deletes return empty 204.
Responses include joined category/teacher names and actual lesson/enrollment counts.

## Configuration and transaction boundaries

Set an explicit `DATABASE_URL` as documented in the root README and apply canonical migrations
separately. Public category listing needs no authentication secret. Protected operations use
identity's current active actor and therefore need identity configuration. The lazily created
engine is shared with identity and disposed by the application lifespan. Each request closes
its own SQLAlchemy session and rolls back any unfinished transaction; write use cases commit
explicitly through a unit of work using that same session. No schema auto-creation occurs.

Parameterized SQL locks existing course/category rows during writes. Category-name MySQL 1062
conflicts become 409 `INVALID_STATE`; restricted category deletion becomes 409 `RESOURCE_IN_USE`.
Course restrictive dependencies become 409 `INVALID_STATE`. Unexpected storage failures return
safe 503 envelopes without driver messages, SQL, parameters or credentials. Dependency checks
cover direct V1–V9 course links (lessons and course members); lesson descendants are thereby
protected, and foreign keys remain the final restricted-delete guard.

## Scope and verification limits

Published or archived courses can be read but cannot be patched/deleted in TASK005. This is a
scope restriction, not a final decision about eventual published-edit policy. No marketplace,
publication/archive workflow, enrollment workflow, thumbnail upload, lessons or AI are added.
Draft price storage does not implement paid enrollment or authorize paid publication.

Offline checks:

```sh
uv run pytest tests/test_catalog_course_domain.py tests/test_catalog_course_api.py tests/test_catalog_course_persistence.py
uv run ruff check .
uv run ruff format --check .
```

API tests use real domain services with fake repositories/unit of work; persistence tests inspect
parameterized statements and mock SQLAlchemy sessions/driver conflicts. They are not SQLite
substitutes and do not prove MySQL locking, concurrent races, rollback persistence or collation
behavior. No database was contacted for TASK005. Real disposable MySQL CRUD/concurrency tests,
migration acceptance and Java/Python parity remain pending explicit database authorization.
