import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from app.core.api_errors import ApiError
from app.core.errors import register_error_handlers
from app.core.request_id import RequestIdMiddleware
from app.modules.identity.api.dependencies import current_actor
from app.modules.lesson.api import get_lesson_service, router
from app.modules.lesson.domain import CourseAccess, Lesson, LessonService


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    actor = SimpleNamespace(id=2, role="TEACHER")
    repo, courses, uow = Mock(), Mock(), Mock()
    uow.__enter__ = Mock(return_value=uow)
    uow.__exit__ = Mock(return_value=False)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repo.get.return_value = Lesson(
        4, 1, "Title", transcript_text="secret", created_at=now, updated_at=now
    )
    repo.list.return_value = [repo.get.return_value]
    repo.next_order.return_value = 1
    repo.dependencies.return_value = False
    repo.save.side_effect = lambda item: replace(item, id=4, created_at=now, updated_at=now)
    courses.get.return_value = CourseAccess(1, 2)
    app.dependency_overrides[current_actor] = lambda: actor
    app.dependency_overrides[get_lesson_service] = lambda: LessonService(repo, courses, lambda: uow)
    with TestClient(app) as http:
        yield http, actor, courses, app


def test_actual_lesson_response_matches_canonical_schema(client):
    contract = json.loads((Path(__file__).parents[1] / "contracts/openapi.yaml").read_text())
    validator = Draft202012Validator(
        {"$ref": "#/components/schemas/Lesson", "components": contract["components"]},
        format_checker=FormatChecker(),
    )
    http, actor, courses, _ = client
    response = http.post("/api/v1/courses/1/lessons", json={"title": "Title"})
    assert response.status_code == 201
    validator.validate(response.json())
    actor.id, actor.role = 3, "STUDENT"
    courses.get.return_value = CourseAccess(1, 2, enrolled=True)
    body = http.get("/api/v1/lessons/4").json()
    validator.validate(body)
    assert body["transcript_text"] is None


def test_create_get_list_put_delete_and_wire(client):
    http, _, _, _ = client
    response = http.post("/api/v1/courses/1/lessons", json={"title": " Title "})
    assert response.status_code == 201
    assert response.headers["Location"] == "/api/v1/lessons/4"
    body = response.json()
    assert body["id"] == "4" and body["course_id"] == "1"
    assert body["created_at"] == "2026-01-01T00:00:00Z"
    assert "generation_count" not in body
    assert http.get("/api/v1/courses/1/lessons").json()[0]["title"] == "Title"
    assert http.get("/api/v1/lessons/4").json()["transcript_text"] == "secret"
    assert (
        http.put("/api/v1/lessons/4", json={"title": "Replacement"}).json()["transcript_text"]
        is None
    )
    assert (
        http.put(
            "/api/v1/lessons/4",
            json={"title": "Replacement", "video_url": None, "transcript_text": None},
        ).json()["video_url"]
        == ""
    )
    assert http.delete("/api/v1/lessons/4").status_code == 204
    assert http.patch("/api/v1/lessons/4", json={"title": "x"}).status_code == 405


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": None},
        {"title": 2},
        {"title": " "},
        {"title": "x", "order_index": 1},
        {"title": "x", "course_id": 1},
        {"title": "x", "video_url": " " * 2049},
    ],
)
def test_invalid_payload_400_envelope(client, payload):
    response = client[0].post("/api/v1/courses/1/lessons", json=payload)
    assert response.status_code == 400
    assert set(response.json()) == {"code", "message", "details", "request_id"}
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


@pytest.mark.parametrize("id", ["0", "-1", "9223372036854775808", "abc"])
def test_bounded_bigint_path(client, id):
    assert client[0].get(f"/api/v1/lessons/{id}").status_code == 400


def test_student_enrollment_role_and_transcript(client):
    http, actor, courses, _ = client
    actor.id, actor.role = 3, "STUDENT"
    assert http.get("/api/v1/lessons/4").status_code == 403
    courses.get.return_value = CourseAccess(1, 2, enrolled=True)
    for body in [
        http.get("/api/v1/lessons/4").json(),
        http.get("/api/v1/courses/1/lessons").json()[0],
    ]:
        assert body["transcript_text"] is None and body["can_delete"] is False
        assert "generation_count" not in body
    assert http.post("/api/v1/courses/1/lessons", json={"title": "x"}).status_code == 403
    actor.id, actor.role = 9, "ADMIN"
    assert http.get("/api/v1/lessons/4").json()["transcript_text"] == "secret"


def test_identity_boundary_unauthenticated(client):
    http, _, _, app = client

    def unauthenticated():
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")

    app.dependency_overrides[current_actor] = unauthenticated
    assert http.get("/api/v1/lessons/4").status_code == 401
