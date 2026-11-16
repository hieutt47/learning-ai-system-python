"""Offline tests for course publication, archive, and public marketplace."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.domain import DomainError
from app.main import create_app
from app.modules.course.api import get_course_service, get_marketplace_service
from app.modules.course.domain import Course, CourseService, MarketplaceService
from app.modules.identity.api.dependencies import current_actor
from app.modules.identity.domain.models import Actor

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)


class FakeUoW:
    def __init__(self) -> None:
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass

    def commit(self) -> None:
        self.committed = True


class FakeCourseRepo:
    def __init__(self) -> None:
        self.courses: dict[int, Course] = {
            1: Course(
                id=1,
                title="Free Ready",
                description="Desc",
                category_id=1,
                teacher_id=10,
                price=Decimal("0.00"),
                is_published=False,
                thumbnail_url="/api/v1/files/abc.png",
                thumbnail_storage_key="abc.png",
                created_at=NOW,
                updated_at=NOW,
                category_name="Cat",
                teacher_name="Teacher",
                lesson_count=1,
                total_students=0,
            ),
            2: Course(
                id=2,
                title="No Thumb",
                description="Desc",
                category_id=1,
                teacher_id=10,
                price=Decimal("0.00"),
                created_at=NOW,
                updated_at=NOW,
                category_name="Cat",
                teacher_name="Teacher",
                lesson_count=1,
                total_students=0,
            ),
            3: Course(
                id=3,
                title="Paid",
                description="Desc",
                category_id=1,
                teacher_id=10,
                price=Decimal("10000.00"),
                thumbnail_url="/api/v1/files/abc.png",
                thumbnail_storage_key="abc.png",
                created_at=NOW,
                updated_at=NOW,
                category_name="Cat",
                teacher_name="Teacher",
                lesson_count=1,
                total_students=0,
            ),
            4: Course(
                id=4,
                title="No Lessons",
                description="Desc",
                category_id=1,
                teacher_id=10,
                price=Decimal("0.00"),
                thumbnail_url="/api/v1/files/abc.png",
                thumbnail_storage_key="abc.png",
                created_at=NOW,
                updated_at=NOW,
                category_name="Cat",
                teacher_name="Teacher",
                lesson_count=0,
                total_students=0,
            ),
        }

    def get(self, course_id: int, *, for_update: bool = False) -> Course | None:
        return self.courses.get(course_id)

    def set_published(self, course_id: int, is_published: bool) -> Course:
        c = self.courses[course_id]
        updated = Course(
            **{**c.__dict__, "is_published": is_published},
        )
        self.courses[course_id] = updated
        return updated

    def archive(self, course_id: int) -> Course:
        c = self.courses[course_id]
        updated = Course(
            **{**c.__dict__, "is_published": False, "archived_at": NOW},
        )
        self.courses[course_id] = updated
        return updated

    def search_public(self, *, q, category_id, sort, page, page_size):
        items = [
            {
                "id": str(c.id),
                "title": c.title,
                "description": c.description,
                "category": {"id": str(c.category_id), "name": c.category_name},
                "teacher": {"id": str(c.teacher_id), "full_name": c.teacher_name},
                "thumbnail_url": c.thumbnail_url,
                "price": format(c.price, ".2f"),
                "currency": "VND",
                "total_students": c.total_students,
                "lesson_count": c.lesson_count,
                "outline": None,
            }
            for c in self.courses.values()
            if c.is_published
            and c.archived_at is None
            and (category_id is None or c.category_id == category_id)
            and (q is None or q.lower() in c.title.lower())
        ]
        return items[(page - 1) * page_size : page * page_size], len(items)

    def get_public(self, course_id: int) -> dict | None:
        c = self.courses.get(course_id)
        if c is None or not c.is_published or c.archived_at is not None:
            return None
        return {
            "id": str(c.id),
            "title": c.title,
            "description": c.description,
            "category": {"id": str(c.category_id), "name": c.category_name},
            "teacher": {"id": str(c.teacher_id), "full_name": c.teacher_name},
            "thumbnail_url": c.thumbnail_url,
            "price": format(c.price, ".2f"),
            "currency": "VND",
            "total_students": c.total_students,
            "lesson_count": 1,
            "outline": [{"id": "101", "title": "Intro", "order_index": 0}],
        }


class FakeCategoryLookup:
    def exists(self, category_id: int, *, for_update: bool = False) -> bool:
        return category_id == 1


def _actor(uid: int, role: str) -> Actor:
    return Actor(
        id=uid,
        full_name="User",
        email=f"u{uid}@example.invalid",
        role=role,
        is_active=True,
        created_at=NOW,
    )


def test_publication_conditions_and_archive_invariants():
    repo = FakeCourseRepo()
    svc = CourseService(repo, FakeCategoryLookup(), FakeUoW)
    teacher = _actor(10, "TEACHER")

    with pytest.raises(DomainError) as exc_thumb:
        svc.set_published(teacher, 2, True)
    assert exc_thumb.value.code == "INVALID_STATE"

    with pytest.raises(DomainError) as exc_paid:
        svc.set_published(teacher, 3, True)
    assert exc_paid.value.code == "COURSE_NOT_FREE"

    with pytest.raises(DomainError) as exc_lesson:
        svc.set_published(teacher, 4, True)
    assert exc_lesson.value.code == "INVALID_STATE"

    published = svc.set_published(teacher, 1, True)
    assert published.is_published is True

    archived = svc.archive(teacher, 1)
    assert archived.is_published is False
    assert archived.archived_at is not None

    with pytest.raises(DomainError) as exc_repub:
        svc.set_published(teacher, 1, True)
    assert exc_repub.value.code == "INVALID_STATE"


def test_marketplace_routes_sort_whitelist_and_visibility():
    repo = FakeCourseRepo()
    course_svc = CourseService(repo, FakeCategoryLookup(), FakeUoW)
    market_svc = MarketplaceService(repo, FakeCategoryLookup())
    app = create_app(Settings(_env_file=None, environment="test"))
    app.dependency_overrides[current_actor] = lambda: _actor(10, "TEACHER")
    app.dependency_overrides[get_course_service] = lambda: course_svc
    app.dependency_overrides[get_marketplace_service] = lambda: market_svc

    with TestClient(app) as client:
        # Draft not visible publicly
        assert client.get("/api/v1/courses").json()["total"] == 0
        assert client.get("/api/v1/courses/1").status_code == 404

        # Publish course 1
        pub = client.patch("/api/v1/courses/1/publication", json={"is_published": True})
        assert pub.status_code == 200
        assert pub.json()["is_published"] is True

        # Public marketplace & detail
        search = client.get("/api/v1/courses", params={"q": "Free", "sort": "title,asc"})
        assert search.status_code == 200
        assert search.json()["total"] == 1
        assert search.json()["items"][0]["outline"] is None

        detail = client.get("/api/v1/courses/1")
        assert detail.status_code == 200
        assert detail.json()["outline"] == [{"id": "101", "title": "Intro", "order_index": 0}]

        # Invalid sort or missing category rejected
        assert client.get("/api/v1/courses", params={"sort": "evil_sql"}).status_code == 400
        assert client.get("/api/v1/courses", params={"category_id": "999"}).status_code == 404

        # Archive with archived=false rejected; archived=true hides from marketplace
        assert (
            client.patch("/api/v1/courses/1/archive", json={"archived": False}).status_code == 400
        )
        arch = client.patch("/api/v1/courses/1/archive", json={"archived": True})
        assert arch.status_code == 200
        assert client.get("/api/v1/courses").json()["total"] == 0
        assert client.get("/api/v1/courses/1").status_code == 404
