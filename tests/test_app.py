from uuid import UUID

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.main import create_app


class ExamplePayload(BaseModel):
    quantity: int = Field(gt=0)


@pytest.fixture
def client():
    application = create_app(Settings(_env_file=None, name="Test API", environment="test"))
    router = APIRouter()

    @router.post("/_test/validate")
    async def validate(payload: ExamplePayload):
        return payload

    @router.get("/_test/forbidden")
    async def forbidden():
        raise HTTPException(status_code=403, detail="Access denied.")

    @router.get("/_test/unauthorized")
    async def unauthorized():
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @router.get("/_test/failure")
    async def failure():
        raise RuntimeError("Internal sensitive detail")

    application.include_router(router)
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client


def assert_envelope(response, expected_status, expected_code):
    assert response.status_code == expected_status
    body = response.json()
    assert set(body) == {"code", "message", "details", "request_id"}
    assert body["code"] == expected_code
    assert isinstance(body["message"], str)
    assert isinstance(body["details"], list)
    assert str(UUID(body["request_id"])) == body["request_id"]
    assert response.headers["X-Request-ID"] == body["request_id"]
    return body


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert UUID(response.headers["X-Request-ID"]).version == 4


def test_request_ids_are_generated_and_unique(client):
    supplied_id = "untrusted-" + "x" * 10000
    first = client.get("/health", headers={"X-Request-ID": supplied_id})
    second = client.get("/health", headers={"X-Request-ID": supplied_id})
    assert first.headers["X-Request-ID"] != supplied_id
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    assert UUID(first.headers["X-Request-ID"]).version == 4


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/missing", 404, "NOT_FOUND"),
        ("/_test/forbidden", 403, "FORBIDDEN"),
        ("/_test/unauthorized", 401, "UNAUTHORIZED"),
    ],
)
def test_http_error_envelopes(client, path, status, code):
    body = assert_envelope(client.get(path), status, code)
    assert body["details"] == []


def test_method_not_allowed_uses_envelope(client):
    response = client.post("/health")
    assert_envelope(response, 405, "METHOD_NOT_ALLOWED")
    assert "GET" in response.headers["Allow"]


def test_http_error_preserves_authentication_header(client):
    response = client.get("/_test/unauthorized")
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_unexpected_error_is_safe(client):
    response = client.get("/_test/failure")
    body = assert_envelope(response, 500, "INTERNAL_SERVER_ERROR")
    assert body["message"] == "An unexpected error occurred."
    assert "sensitive" not in response.text


@pytest.mark.parametrize("payload", [{}, {"quantity": 0}, {"quantity": "secret-input"}])
def test_validation_uses_400_without_echoing_input(client, payload):
    response = client.post("/_test/validate", json=payload)
    body = assert_envelope(response, 400, "VALIDATION_ERROR")
    assert body["details"][0]["field"] == "quantity"
    assert set(body["details"][0]) == {"field", "message"}
    assert body["details"][0]["message"] == "Invalid value"
    assert "secret-input" not in response.text


def test_validation_does_not_echo_unrelated_sensitive_fields(client):
    response = client.post("/_test/validate", json={"password": "private-value"})
    assert_envelope(response, 400, "VALIDATION_ERROR")
    assert "private-value" not in response.text
    assert "password" not in response.text


def test_malformed_json_uses_400(client):
    response = client.post(
        "/_test/validate", content="{invalid", headers={"Content-Type": "application/json"}
    )
    assert_envelope(response, 400, "VALIDATION_ERROR")


def test_valid_body(client):
    response = client.post("/_test/validate", json={"quantity": 1})
    assert response.status_code == 200
    assert response.json() == {"quantity": 1}


def test_default_settings_are_safe(monkeypatch):
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.name == "Learning AI System API"
    assert settings.environment == "development"


def test_settings_can_be_injected():
    settings = Settings(_env_file=None, name="Injected API", environment="test")
    application = create_app(settings)
    assert application.title == "Injected API"
    assert application.state.settings is settings
    assert application.debug is False


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Environment API")
    monkeypatch.setenv("APP_ENVIRONMENT", "test")
    settings = Settings(_env_file=None)
    assert settings.name == "Environment API"
    assert settings.environment == "test"


def test_test_routes_do_not_exist_in_production_app():
    application = create_app(Settings(_env_file=None, environment="test"))
    with TestClient(application) as test_client:
        assert test_client.get("/_test/failure").status_code == 404
