import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy.exc import SQLAlchemyError
from test_catalog_course_domain import (
    ADMIN,
    OTHER,
    STUDENT,
    TEACHER,
    Categories,
    Courses,
    Transaction,
)

from app.core.api_errors import ApiError
from app.core.config import Settings
from app.main import create_app
from app.modules.catalog.api import get_catalog_service
from app.modules.catalog.domain import CatalogService
from app.modules.course.api import get_course_service
from app.modules.course.domain import CourseService
from app.modules.identity.api.dependencies import current_actor


@pytest.fixture
def api():
    app = create_app(Settings(_env_file=None))
    categories, courses = Categories(), Courses()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    courses.rows[1] = replace(
        courses.rows[1],
        category_name="Science",
        teacher_name="Teacher",
        lesson_count=2,
        total_students=3,
        created_at=now,
        updated_at=now,
    )
    original_save = courses.save

    def save_with_projection(course):
        return original_save(
            replace(
                course,
                category_name="Science",
                teacher_name="Teacher",
                lesson_count=course.lesson_count or 0,
                total_students=course.total_students or 0,
                created_at=course.created_at or now,
                updated_at=now,
            )
        )

    courses.save = save_with_projection
    tx = Transaction(categories, courses)
    actor = [TEACHER]
    app.dependency_overrides[get_catalog_service] = lambda: CatalogService(categories, tx)
    app.dependency_overrides[get_course_service] = lambda: CourseService(courses, categories, tx)
    app.dependency_overrides[current_actor] = lambda: actor[0]
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, app=app, actor=actor, categories=categories, courses=courses, tx=tx
        )


def body(**changes):
    return {
        "title": " Title ",
        "description": " Description ",
        "category_id": "1",
        "price": "0.00",
        **changes,
    }


def test_public_categories_need_no_actor_or_auth_secret(api):
    def forbidden():
        raise AssertionError("Public listing must not resolve current_actor")

    api.app.dependency_overrides[current_actor] = forbidden
    response = api.client.get("/api/v1/categories?page=1&page_size=1")
    assert response.status_code == 200
    assert response.json() == {
        "items": [{"id": "1", "name": "Science", "description": "Description"}],
        "page": 1,
        "page_size": 1,
        "total": 1,
    }


def test_category_admin_writes_conflicts_and_delete(api):
    assert api.client.post("/api/v1/categories", json={"name": "New"}).status_code == 403
    api.actor[0] = ADMIN
    response = api.client.post("/api/v1/categories", json={"name": " New "})
    assert response.status_code == 201 and response.json()["name"] == "New"
    assert api.client.post("/api/v1/categories", json={"name": "science"}).status_code == 409
    assert api.client.patch("/api/v1/categories/2", json={"name": None}).json()["name"] == "New"
    api.categories.used = True
    assert api.client.delete("/api/v1/categories/1").json()["code"] == "RESOURCE_IN_USE"
    api.categories.used = False
    response = api.client.delete("/api/v1/categories/2")
    assert response.status_code == 204 and response.content == b""


def test_course_creation_projection_location_and_patch_noops(api):
    response = api.client.post("/api/v1/courses", json=body())
    assert response.status_code == 201
    assert response.headers["location"] == "/api/v1/teaching/courses/2"
    assert response.json()["teacher_id"] == "2" and response.json()["price"] == "0.00"
    result = api.client.get("/api/v1/teaching/courses/1").json()
    assert result["lesson_count"] == 2 and result["total_students"] == 3
    assert result["created_at"] == "2026-01-01T00:00:00Z"
    assert result["category_name"] == "Science" and result["teacher_name"] == "Teacher"
    assert (
        api.client.patch("/api/v1/courses/1", json={"title": None, "price": None}).status_code
        == 200
    )
    assert (
        api.client.patch("/api/v1/courses/1", json={"title": " Renamed "}).json()["title"]
        == "Renamed"
    )
    assert api.client.delete("/api/v1/courses/2").status_code == 204


@pytest.mark.parametrize("actor", [STUDENT, OTHER])
def test_course_authorization_uses_real_domain(api, actor):
    api.actor[0] = actor
    for method, path, kwargs in [
        ("get", "/api/v1/teaching/courses/1", {}),
        ("patch", "/api/v1/courses/1", {"json": {"title": "Changed"}}),
        ("delete", "/api/v1/courses/1", {}),
    ]:
        assert getattr(api.client, method)(path, **kwargs).status_code == 403
    if actor is STUDENT:
        assert api.client.get("/api/v1/teaching/courses").status_code == 403
        assert api.client.post("/api/v1/courses", json=body()).status_code == 403
    else:
        assert api.client.get("/api/v1/teaching/courses").json()["total"] == 0


def test_admin_scope_missing_and_draft_only(api):
    api.actor[0] = ADMIN
    assert api.client.get("/api/v1/teaching/courses").json()["total"] == 1
    assert api.client.get("/api/v1/teaching/courses/999").status_code == 404
    assert api.client.post("/api/v1/courses", json=body(category_id="999")).status_code == 404
    for changes in ({"is_published": True}, {"archived_at": datetime.now(UTC)}):
        api.courses.rows[1] = replace(api.courses.rows[1], **changes)
        assert api.client.patch("/api/v1/courses/1", json={}).json()["code"] == "INVALID_STATE"
        assert api.client.delete("/api/v1/courses/1").status_code == 409


@pytest.mark.parametrize(
    "price",
    [0, 1.2, True, "-1", "NaN", "Infinity", "1e2", "0.001", "1000000000000", " 0", "+1", "1.", ""],
)
def test_strict_decimal_string_price(api, price):
    assert api.client.post("/api/v1/courses", json=body(price=price)).status_code == 400
    assert api.client.patch("/api/v1/courses/1", json={"price": price}).status_code == 400


@pytest.mark.parametrize("value", ["0", "-1", "01", "1.0", "9223372036854775808", "abc"])
def test_bigint_wire_validation(api, value):
    assert api.client.get(f"/api/v1/teaching/courses/{value}").status_code == 400
    assert api.client.post("/api/v1/courses", json=body(category_id=value)).status_code == 400


@pytest.mark.parametrize(
    "change",
    [
        {"category_id": 1},
        {"teacher_id": "99"},
        {"is_published": True},
        {"thumbnail_url": "url"},
        {"title": " "},
        {"title": "x" * 201},
        {"description": " "},
    ],
)
def test_course_strict_payloads(api, change):
    response = api.client.post("/api/v1/courses", json=body(**change))
    assert response.status_code == 400
    assert set(response.json()) == {"code", "message", "details", "request_id"}


def test_pagination_validation_and_safe_storage_failure(api):
    for query in ("page=0", "page_size=101", "page=abc"):
        assert api.client.get(f"/api/v1/categories?{query}").status_code == 400
    failing = Mock()
    failing.list.side_effect = SQLAlchemyError("secret SQL password")
    api.app.dependency_overrides[get_catalog_service] = lambda: failing
    response = api.client.get("/api/v1/categories")
    assert response.status_code == 503 and "secret" not in response.text


def test_actual_http_responses_match_canonical_schemas(api):
    contract = json.loads((Path(__file__).parents[1] / "contracts/openapi.yaml").read_text())
    for name, response in [
        ("CategoryPage", api.client.get("/api/v1/categories")),
        ("CoursePage", api.client.get("/api/v1/teaching/courses")),
        ("Course", api.client.post("/api/v1/courses", json=body())),
    ]:
        assert response.status_code in {200, 201}
        Draft202012Validator(
            {"$ref": f"#/components/schemas/{name}", "components": contract["components"]},
            format_checker=FormatChecker(),
        ).validate(response.json())


def test_unauthenticated_boundary(api):
    def missing():
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")

    api.app.dependency_overrides[current_actor] = missing
    assert api.client.get("/api/v1/teaching/courses").status_code == 401
