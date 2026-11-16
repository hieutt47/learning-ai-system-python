"""Tests for safe course thumbnail upload, magic-byte validation, and file serving."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.domain import DomainError
from app.main import create_app
from app.modules.course.api import get_course_service, get_thumbnail_storage
from app.modules.course.domain import Course, CourseService
from app.modules.course.storage import MAX_THUMBNAIL_BYTES, LocalThumbnailStorage
from app.modules.identity.api.dependencies import current_actor
from app.modules.identity.domain.models import Actor

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
)


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
    def __init__(self, *, fail_save: bool = False) -> None:
        self.fail_save = fail_save
        self.course = Course(
            id=1,
            title="Course",
            description="Desc",
            category_id=1,
            teacher_id=10,
            price=Decimal("0.00"),
            created_at=NOW,
            updated_at=NOW,
            category_name="Cat",
            teacher_name="Teacher",
            lesson_count=0,
            total_students=0,
        )

    def get(self, course_id: int, *, for_update: bool = False) -> Course | None:
        return self.course if course_id == 1 else None

    def save_thumbnail(self, course_id: int, storage_key: str, thumbnail_url: str) -> Course:
        if self.fail_save:
            raise DomainError("INTERNAL_ERROR", 500, "Simulated DB failure")
        self.course = Course(
            id=self.course.id,
            title=self.course.title,
            description=self.course.description,
            category_id=self.course.category_id,
            teacher_id=self.course.teacher_id,
            price=self.course.price,
            is_published=self.course.is_published,
            archived_at=self.course.archived_at,
            thumbnail_url=thumbnail_url,
            created_at=self.course.created_at,
            updated_at=self.course.updated_at,
            category_name=self.course.category_name,
            teacher_name=self.course.teacher_name,
            lesson_count=self.course.lesson_count,
            total_students=self.course.total_students,
            thumbnail_storage_key=storage_key,
        )
        return self.course


class DummyCatLookup:
    def exists(self, category_id: int, *, for_update: bool = False) -> bool:
        return True


def _actor(uid: int, role: str) -> Actor:
    return Actor(
        id=uid,
        full_name="User",
        email=f"u{uid}@example.invalid",
        role=role,
        is_active=True,
        created_at=NOW,
    )


def test_thumbnail_upload_serve_and_replace(tmp_path: Path):
    storage = LocalThumbnailStorage(tmp_path)
    repo = FakeCourseRepo()
    svc = CourseService(repo, DummyCatLookup(), FakeUoW)
    app = create_app(Settings(_env_file=None, environment="test"))
    app.dependency_overrides[current_actor] = lambda: _actor(10, "TEACHER")
    app.dependency_overrides[get_course_service] = lambda: svc
    app.dependency_overrides[get_thumbnail_storage] = lambda: storage

    with TestClient(app) as client:
        res1 = client.post(
            "/api/v1/courses/1/thumbnail",
            files={"file": ("thumb.png", PNG_BYTES, "image/png")},
        )
        assert res1.status_code == 200
        url1 = res1.json()["thumbnail_url"]
        assert url1.startswith("/api/v1/files/")
        key1 = url1.rsplit("/", 1)[-1]
        assert (tmp_path / key1).is_file()

        served = client.get(url1)
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/png"
        assert served.headers["x-content-type-options"] == "nosniff"
        assert served.content == PNG_BYTES

        # Replace deletes old thumbnail after commit
        res2 = client.post(
            "/api/v1/courses/1/thumbnail",
            files={"file": ("thumb2.png", PNG_BYTES, "image/png")},
        )
        assert res2.status_code == 200
        assert not (tmp_path / key1).exists()


def test_thumbnail_validation_traversal_and_rollback_cleanup(tmp_path: Path):
    storage = LocalThumbnailStorage(tmp_path)
    repo = FakeCourseRepo(fail_save=True)
    svc = CourseService(repo, DummyCatLookup(), FakeUoW)
    app = create_app(Settings(_env_file=None, environment="test"))
    app.dependency_overrides[current_actor] = lambda: _actor(10, "TEACHER")
    app.dependency_overrides[get_course_service] = lambda: svc
    app.dependency_overrides[get_thumbnail_storage] = lambda: storage

    with TestClient(app) as client:
        # Spoofed MIME type rejected
        bad_type = client.post(
            "/api/v1/courses/1/thumbnail",
            files={"file": ("fake.png", b"not-an-image", "image/png")},
        )
        assert bad_type.status_code == 415

        # Over 5 MiB rejected
        too_big = client.post(
            "/api/v1/courses/1/thumbnail",
            files={"file": ("big.png", PNG_BYTES + b"0" * MAX_THUMBNAIL_BYTES, "image/png")},
        )
        assert too_big.status_code == 413

        # Traversal rejected
        assert client.get("/api/v1/files/..%2Fsecret.png").status_code == 404
        assert client.get("/api/v1/files/not-a-valid-key.png").status_code == 404

        # Rollback cleanup on DB failure leaves 0 orphan files
        failed = client.post(
            "/api/v1/courses/1/thumbnail",
            files={"file": ("thumb.png", PNG_BYTES, "image/png")},
        )
        assert failed.status_code == 500
        assert list(tmp_path.iterdir()) == []
