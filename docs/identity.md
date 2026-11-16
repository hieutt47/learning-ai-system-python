# Minimal identity (TASK004)

Implemented scope: login, cookie refresh/logout and current user. Registration, email,
password-reset/profile/admin flows are not exposed. The partial canonical contract is
[contracts/openapi.yaml](../contracts/openapi.yaml); framework spec routes are disabled.

## Configuration and database

Health works without secrets or database configuration. Auth returns safe 503
CONFIGURATION_ERROR when required settings are absent/invalid; storage outages return
503 SERVICE_UNAVAILABLE. No database connection or schema generation occurs at startup.
Set DATABASE_URL as described in the root README and migrate the separate authorized
MySQL database through official Flyway first. Never use the Java application's database.

Set AUTH_JWT_SECRET to independently generated cryptographically random material of at
least 32 UTF-8 bytes. There is no default secret. Set AUTH_COOKIE_SECURE=true for HTTPS;
APP_ENVIRONMENT=production or AUTH_ENVIRONMENT=production refuses insecure auth cookies.
AUTH_CORS_ORIGINS is a JSON array of exact trusted HTTP(S) origins (no paths/wildcard).
Default [] permits no cross-origin credentialed requests. Prefer a same-origin frontend
proxy or same-site deployment; cross-site cookies/CSRF require separate approval.
Keep secrets in environment/ignored .env and out of Git, logs and reports.

## Semantics

Login uses trim/lowercase email, unchanged passwords and BCrypt cost-10-compatible
$2a$/$2b$ hashes. Passwords over 72 UTF-8 bytes are rejected, never silently truncated.
Missing users use dummy BCrypt work. Credentials must match and user must be active with
email_verified_at set. Minimal development fixtures must be provisioned separately with
valid BCrypt hashes and verified/active STUDENT, TEACHER and ADMIN users as needed.
There is no public account-creation route or default account/password.

Access JWTs use fixed HS256 with required sub=email, iat and exp; expiry is 15 minutes.
Current user and permissions reload DB user/role, never trust the token's role claim.
Access JWTs remain usable until expiry after logout if the account is active.
Refresh token is 32 random bytes, unpadded Base64url; only its SHA-256 hex is stored.
Refresh locks its session row, validates before rotation, replaces hash in the same row,
and slides expiry by seven days; expired/revoked sessions cannot be revived. UoW rolls
back uncommitted work and closes. The cookie is HttpOnly, SameSite=Lax, path /api/v1/auth,
Max-Age=604800 (Secure when configured). Logout is repeatable 204, revokes if found and
clears the same cookie with Max-Age=0; it does not revoke issued JWTs.

Frontend sends credentials: include, no Authorization on login/refresh/logout, and fetches
/users/me separately after login/refresh. A valid unexpired Bearer for an inactive user
on refresh/logout produces 403 before cookie/session mutation; invalid/expired Bearer
does not block cookie processing. Protected requests require valid Bearer access JWT.
All errors contain code/message/details/request_id and X-Request-ID; validation is 400.

## Evidence and limits

Run uv run pytest and uv run ruff check . / uv run ruff format --check . Offline tests
cover domain rollback/failures, cookies/status/envelopes, JWT tampering/claim validation,
BCrypt compatibility and SQL compilation with FOR UPDATE. They use fake repositories,
not SQLite. SQL compilation is not real concurrency proof. No identity MySQL runtime,
concurrent refresh race, or full frontend end-to-end test has been executed; do not treat
offline success as production/security readiness. The foundation opt-in MySQL tests remain
separate and require an explicitly authorized disposable target.
