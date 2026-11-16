import json
from pathlib import Path

from fastapi.routing import APIRoute
from openapi_spec_validator import validate

from app.core.config import Settings
from app.main import create_app
from app.modules.identity.api.router import router


def test_partial_canonical_contract_matches_identity_routes_and_shapes():
    contract = json.loads((Path(__file__).parents[1] / "contracts/openapi.yaml").read_text())
    validate(contract)
    expected = {
        (path, method.upper())
        for path, operations in contract["paths"].items()
        for method in operations
        if method in {"get", "post", "patch", "delete"}
        and (path.startswith("/api/v1/auth/") or path == "/api/v1/users/me")
    }
    actual = {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert expected == actual
    schemas = contract["components"]["schemas"]
    assert set(schemas["Login"]["required"]) == {"email", "password"}
    assert schemas["Login"]["additionalProperties"] is False
    assert schemas["AuthResponse"]["required"] == ["access_token"]
    assert set(schemas["Error"]["required"]) == {"code", "message", "details", "request_id"}
    assert schemas["User"]["properties"]["id"]["type"] == "string"
    assert schemas["User"]["properties"]["created_at"]["pattern"] == "Z$"
    assert "204" in contract["paths"]["/api/v1/auth/logout"]["post"]["responses"]
    assert "400" in contract["paths"]["/api/v1/auth/login"]["post"]["responses"]
    schemes = contract["components"]["securitySchemes"]
    assert schemes["refreshCookie"]["in"] == "cookie"
    assert schemes["refreshCookie"]["name"] == "refreshToken"
    assert schemes["bearerAuth"]["scheme"] == "bearer"
    app = create_app(Settings(_env_file=None))
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None
