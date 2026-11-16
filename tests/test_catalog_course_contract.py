import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def contract():
    return json.loads((Path(__file__).parents[1] / "contracts/openapi.yaml").read_text())


def schema_validator(contract, name):
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": contract["components"]},
        format_checker=FormatChecker(),
    )


def test_canonical_spec_covers_all_implemented_business_operations(contract):
    validate(contract)
    expected = {
        (path, method.upper())
        for path, operations in contract["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }
    # FastAPI includes routers lazily; inspect its resolved runtime path inventory.
    # This generated schema is used only for route coverage, not as a second contract.
    runtime_paths = create_app(Settings(_env_file=None)).openapi()["paths"]
    actual = {
        (path, method.upper())
        for path, operations in runtime_paths.items()
        if path.startswith("/api/v1/")
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert expected == actual
    assert len(expected) == 23
    for path, method in expected:
        operation = contract["paths"][path][method.lower()]
        if (path, method) in {
            ("/api/v1/categories", "GET"),
            ("/api/v1/files/{storage_key}", "GET"),
        }:
            assert operation["security"] == []
        elif not path.startswith("/api/v1/auth/"):
            assert operation["security"] == [{"bearerAuth": []}]


def test_course_request_contract_rejects_server_fields_numeric_prices_and_bad_ids(contract):
    validator = schema_validator(contract, "CourseCreate")
    good = {"title": "Course", "description": "Description", "category_id": "1", "price": "0.00"}
    validator.validate(good)
    for bad in [
        {**good, "teacher_id": "2"},
        {**good, "is_published": True},
        {**good, "price": 0},
        {**good, "price": "NaN"},
        {**good, "price": "1e2"},
        {**good, "price": "0.001"},
        {**good, "category_id": 1},
        {**good, "category_id": "0"},
    ]:
        assert list(validator.iter_errors(bad)), bad
    schema_validator(contract, "CoursePatch").validate({"price": None, "title": None})


def test_response_contract_requires_string_prices_and_nonnull_projection(contract):
    category = {"id": "1", "name": "Category", "description": None}
    schema_validator(contract, "Category").validate(category)
    course = {
        "id": "1",
        "title": "Course",
        "description": "Description",
        "category_id": "1",
        "teacher_id": "2",
        "price": "0.00",
        "is_published": False,
        "archived_at": None,
        "thumbnail_url": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "category_name": "Category",
        "teacher_name": "Teacher",
        "lesson_count": 0,
        "total_students": 0,
    }
    validator = schema_validator(contract, "Course")
    validator.validate(course)
    for field in ("created_at", "category_name", "lesson_count", "total_students"):
        assert list(validator.iter_errors({**course, field: None}))
    assert list(validator.iter_errors({**course, "price": 0}))
