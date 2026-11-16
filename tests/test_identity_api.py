from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_identity_domain import USER, FakePasswords, Store

from app.core.config import Settings
from app.main import create_app
from app.modules.identity.api.dependencies import get_identity_service
from app.modules.identity.domain.service import IdentityService
from app.modules.identity.infrastructure.crypto import JwtCodec
from app.modules.identity.infrastructure.settings import AuthSettings

SECRET = "unit-test-only-secret-at-least-32-bytes-long"


@pytest.fixture
def configured():
    settings = AuthSettings(_env_file=None, jwt_secret=SECRET)
    app = create_app(Settings(_env_file=None, environment="test"), auth_settings=settings)
    store = Store()
    service = IdentityService(store, FakePasswords(), JwtCodec(SECRET), lambda: datetime.now(UTC))
    app.dependency_overrides[get_identity_service] = lambda: service
    with TestClient(app) as client:
        yield client, store


def login(client):
    return client.post("/api/v1/auth/login", json={"email": USER.email, "password": " secret "})


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"code", "message", "details", "request_id"}
    assert body["code"] == code
    assert body["request_id"] == response.headers["x-request-id"]
    assert isinstance(body["details"], list)
    return body


def test_login_cookie_refresh_logout_me_and_access_survives_logout(configured):
    client, store = configured
    response = login(client)
    assert response.status_code == 200
    assert set(response.json()) == {"access_token"}
    access = response.json()["access_token"]
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Max-Age=604800" in cookie
    assert "Path=/api/v1/auth" in cookie and "SameSite=lax" in cookie
    old = client.cookies.get("refreshToken")
    me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json() == {
        "id": "1",
        "full_name": USER.full_name,
        "email": USER.email,
        "role": "STUDENT",
        "is_active": True,
        "created_at": USER.created_at.isoformat().replace("+00:00", "Z"),
    }
    refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    assert client.cookies.get("refreshToken") != old
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204 and response.content == b""
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert_error(client.post("/api/v1/auth/refresh"), 401, "UNAUTHENTICATED")
    assert (
        client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {access}"}).status_code
        == 200
    )
    assert store.sessions[0].revoked_at is not None


def test_me_uses_current_database_role_and_rejects_missing_bad_or_inactive_bearer(configured):
    client, store = configured
    token = login(client).json()["access_token"]
    assert_error(client.get("/api/v1/users/me"), 401, "UNAUTHENTICATED")
    response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer bad"})
    assert_error(response, 401, "UNAUTHENTICATED")
    assert response.headers["www-authenticate"] == "Bearer"
    store.users = [replace(USER, role="ADMIN")]
    assert (
        client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}).json()["role"]
        == "ADMIN"
    )
    store.users = [replace(USER, is_active=False)]
    assert_error(
        client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}),
        403,
        "ACCOUNT_INACTIVE",
    )


def test_inactive_bearer_blocks_cookie_logout_but_invalid_bearer_does_not(configured):
    client, store = configured
    access = login(client).json()["access_token"]
    original = store.sessions[0]
    store.users = [replace(USER, is_active=False)]
    for endpoint in ["refresh", "logout"]:
        response = client.post(
            f"/api/v1/auth/{endpoint}", headers={"Authorization": f"Bearer {access}"}
        )
        assert_error(response, 403, "ACCOUNT_INACTIVE")
        assert "set-cookie" not in response.headers
        assert store.sessions == [original]
    response = client.post("/api/v1/auth/logout", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 204
    assert store.sessions[0].revoked_at is not None


def test_wrong_pass_inactive_unverified_and_validation(configured):
    client, store = configured
    response = client.post("/api/v1/auth/login", json={"email": USER.email, "password": "wrong"})
    assert_error(response, 401, "UNAUTHENTICATED")
    assert store.sessions == []
    for changes, code in [
        ({"is_active": False}, "ACCOUNT_INACTIVE"),
        ({"email_verified_at": None}, "EMAIL_NOT_VERIFIED"),
    ]:
        store.users = [replace(USER, **changes)]
        assert_error(login(client), 403, code)
        assert store.sessions == []
    for payload in [
        {},
        {"email": USER.email, "password": "é" * 37},
        {"email": USER.email, "password": "private-value", "role": "ADMIN"},
    ]:
        response = client.post("/api/v1/auth/login", json=payload)
        body = assert_error(response, 400, "VALIDATION_ERROR")
        assert "private-value" not in response.text
        assert all(set(detail) == {"field", "message"} for detail in body["details"])


def test_health_without_auth_configuration_auth_fails_safe(monkeypatch):
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    app = create_app(Settings(_env_file=None, environment="test"))
    app.state.auth_settings = None
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        for method, path, body in [
            ("post", "/auth/login", {"email": USER.email, "password": "x"}),
            ("post", "/auth/refresh", None),
            ("post", "/auth/logout", None),
            ("get", "/users/me", None),
        ]:
            response = client.request(method, "/api/v1" + path, json=body)
            assert_error(response, 503, "CONFIGURATION_ERROR")
            assert "secret" not in response.text.lower()


def test_secure_cookie_and_explicit_cors(configured):
    client, _ = configured
    app = client.app
    app.state.auth_settings = AuthSettings(_env_file=None, jwt_secret=SECRET, cookie_secure=True)
    assert "Secure" in login(client).headers["set-cookie"]
    settings = AuthSettings(
        _env_file=None, jwt_secret=SECRET, cors_origins=["https://ui.example.test"]
    )
    with TestClient(create_app(Settings(_env_file=None), auth_settings=settings)) as cors:
        allowed = cors.options(
            "/api/v1/auth/login",
            headers={"Origin": "https://ui.example.test", "Access-Control-Request-Method": "POST"},
        )
        assert allowed.headers["access-control-allow-origin"] == "https://ui.example.test"
        assert allowed.headers["access-control-allow-credentials"] == "true"
        denied = cors.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "https://evil.example.test",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in denied.headers
