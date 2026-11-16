"""Offline domain, API, and OpenAPI contract tests for enrollment."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openapi_spec_validator import validate

from app.core.config import Settings
from app.core.domain import DomainError
from app.main import create_app
from app.modules.enrollment.api import get_enrollment_service
from app.modules.enrollment.domain import CourseAccess, EnrollmentService, Membership
from app.modules.identity.api.dependencies import current_actor
from app.modules.identity.domain.models import Actor

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)


class FakeUoW:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None or not self.committed:
            self.rolled_back = True

    def commit(self) -> None:
        self.committed = True


class FakeEnrollmentRepo:
    def __init__(self) -> None:
        self.courses: dict[int, CourseAccess] = {
            1: CourseAccess(1, 10, True, None, Decimal("0.00")),
            2: CourseAccess(2, 10, False, None, Decimal("0.00")),
            3: CourseAccess(3, 10, True, NOW, Decimal("0.00")),
            4: CourseAccess(4, 10, True, None, Decimal("50000.00")),
        }
        self.memberships: dict[tuple[int, int], Membership] = {}
        self.locked_calls: list[bool] = []

    def course(self, course_id: int, *, lock: bool = False) -> CourseAccess | None:
        self.locked_calls.append(lock)
        return self.courses.get(course_id)

    def membership(self, course_id: int, student_id: int) -> Membership | None:
        return self.memberships.get((course_id, student_id))

    def add(self, course_id: int, student_id: int) -> Membership:
        m = Membership(course_id, student_id, NOW)
        self.memberships[(course_id, student_id)] = m
        return m

    def mine(self, student_id: int, page: int, page_size: int) -> tuple[list[dict], int]:
        items = [
            {
                "id": str(c.id),
                "title": f"Course {c.id}",
                "description": "Desc",
                "category": {"id": "1", "name": "Cat"},
                "teacher": {"id": str(c.teacher_id), "full_name": "Teacher"},
                "thumbnail_url": None,
                "price": format(c.price, ".2f"),
                "currency": "VND",
                "total_students": 1,
                "lesson_count": 2,
                "outline": None,
                "archived_at": c.archived_at,
                "enrolled_at": m.enrolled_at,
            }
            for (cid, sid), m in self.memberships.items()
            if sid == student_id and (c := self.courses.get(cid)) is not None
        ]
        return items[(page - 1) * page_size : page * page_size], len(items)

    def members(self, course_id: int, page: int, page_size: int) -> tuple[list[dict], int]:
        items = [
            {
                "course_id": str(cid),
                "student_id": str(sid),
                "full_name": f"Student {sid}",
                "enrolled_at": m.enrolled_at,
            }
            for (cid, sid), m in self.memberships.items()
            if cid == course_id
        ]
        return items[(page - 1) * page_size : page * page_size], len(items)


def _actor(uid: int, role: str) -> Actor:
    return Actor(
        id=uid,
        full_name=f"User {uid}",
        email=f"u{uid}@example.invalid",
        role=role,
        is_active=True,
        created_at=NOW,
    )


def test_enrollment_domain_rules_and_retry_after_archive():
    repo = FakeEnrollmentRepo()
    svc = EnrollmentService(repo, FakeUoW)
    student = _actor(20, "STUDENT")

    m1, created1 = svc.enroll(student, 1)
    assert created1 is True
    assert m1.course_id == 1
    assert repo.locked_calls == [True]

    # Archive and unpublish course 1; retry must still return existing membership (created=False).
    repo.courses[1] = CourseAccess(1, 10, False, NOW, Decimal("0.00"))
    m2, created2 = svc.enroll(student, 1)
    assert created2 is False
    assert m2 == m1

    # Draft, archived, paid, missing, non-student
    with pytest.raises(DomainError) as exc_draft:
        svc.enroll(student, 2)
    assert exc_draft.value.code == "NOT_FOUND"

    with pytest.raises(DomainError) as exc_arch:
        svc.enroll(student, 3)
    assert exc_arch.value.code == "NOT_FOUND"

    with pytest.raises(DomainError) as exc_paid:
        svc.enroll(student, 4)
    assert exc_paid.value.code == "COURSE_NOT_FREE"

    with pytest.raises(DomainError) as exc_role:
        svc.enroll(_actor(10, "TEACHER"), 1)
    assert exc_role.value.code == "FORBIDDEN"


def test_enrollment_api_routes_and_permissions():
    repo = FakeEnrollmentRepo()
    svc = EnrollmentService(repo, FakeUoW)
    app = create_app(Settings(_env_file=None, environment="test"))
    current = {"actor": _actor(20, "STUDENT")}
    app.dependency_overrides[current_actor] = lambda: current["actor"]
    app.dependency_overrides[get_enrollment_service] = lambda: svc

    with TestClient(app) as client:
        r1 = client.post("/api/v1/courses/1/enroll")
        assert r1.status_code == 201
        assert r1.json()["course_id"] == "1"
        assert r1.json()["student_id"] == "20"

        r2 = client.post("/api/v1/courses/1/enroll")
        assert r2.status_code == 200

        mine = client.get("/api/v1/users/me/courses")
        assert mine.status_code == 200
        body = mine.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "1"
        assert body["items"][0]["currency"] == "VND"
        assert "enrolled_at" in body["items"][0]

        # Student cannot view course members
        assert client.get("/api/v1/courses/1/members").status_code == 403

        # Other teacher cannot view course members
        current["actor"] = _actor(99, "TEACHER")
        assert client.get("/api/v1/courses/1/members").status_code == 403

        # Owner teacher and admin can view course members
        current["actor"] = _actor(10, "TEACHER")
        members_owner = client.get("/api/v1/courses/1/members")
        assert members_owner.status_code == 200
        assert members_owner.json()["items"][0]["student_id"] == "20"

        current["actor"] = _actor(1, "ADMIN")
        assert client.get("/api/v1/courses/1/members").status_code == 200


def test_openapi_spec_is_valid_with_enrollment():
    spec_path = Path(__file__).resolve().parents[1] / "contracts" / "openapi.yaml"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    validate(spec)
    assert "/api/v1/courses/{course_id}/enroll" in spec["paths"]
    assert "/api/v1/users/me/courses" in spec["paths"]
    assert "/api/v1/courses/{course_id}/members" in spec["paths"]
