"""Opt-in real MySQL integration tests for identity, catalog, course, and lesson."""

import os
import secrets

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.core.config import Settings
from app.core.database import database_engine, session_scope
from app.core.migrations import run_flyway
from app.main import create_app
from app.modules.identity.infrastructure.settings import AuthSettings
from tests.test_mysql import require_test_database


@pytest.fixture(scope="module")
def mysql_app_env():
    if os.environ.get("RUN_MYSQL_TESTS") != "1":
        pytest.skip(
            "Real MySQL pending: requires RUN_MYSQL_TESTS=1 and disposable TEST_DATABASE_URL."
        )
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("Explicit TEST_DATABASE_URL is required; no application-URL fallback.")
    db_settings = require_test_database(value)
    engine = database_engine(db_settings)
    try:
        tables = inspect(engine).get_table_names()
        if not tables:
            run_flyway("migrate", db_settings)
        run_flyway("validate", db_settings)
        yield engine, value
    finally:
        engine.dispose()


@pytest.fixture
def mysql_client(mysql_app_env, monkeypatch):
    engine, db_url = mysql_app_env
    monkeypatch.setenv("DATABASE_URL", db_url)
    auth_settings = AuthSettings(
        _env_file=None,
        jwt_secret=secrets.token_hex(32),
        cookie_secure=False,
        environment="test",
    )
    app = create_app(
        Settings(_env_file=None, environment="test"),
        auth_settings=auth_settings,
    )
    with TestClient(app) as client:
        yield client, engine


def _seed_user(engine, *, email: str, password: str, role: str, full_name: str) -> int:
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")
    with session_scope(engine) as session:
        existing = session.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email},
        ).scalar_one_or_none()
        if existing is not None:
            return int(existing)
        session.execute(
            text(
                "INSERT INTO users (full_name, email, password_hash, role, is_active, "
                "email_verified_at) VALUES (:name, :email, :hash, :role, TRUE, UTC_TIMESTAMP(6))"
            ),
            {"name": full_name, "email": email, "hash": pw_hash, "role": role},
        )
        return int(
            session.execute(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": email},
            ).scalar_one()
        )


@pytest.mark.mysql
def test_mysql_auth_catalog_course_lesson_roundtrip(mysql_client):
    client, engine = mysql_client
    suffix = secrets.token_hex(4)
    admin_email = f"admin.{suffix}@example.invalid"
    teacher_email = f"teacher.{suffix}@example.invalid"
    student_email = f"student.{suffix}@example.invalid"
    pw = "ValidPassw0rd!"

    _seed_user(engine, email=admin_email, password=pw, role="ADMIN", full_name="Admin User")
    _seed_user(engine, email=teacher_email, password=pw, role="TEACHER", full_name="Teacher User")
    student_id = _seed_user(
        engine, email=student_email, password=pw, role="STUDENT", full_name="Student User"
    )

    # 1. Identity: login, /users/me, refresh rotation, logout
    login_admin = client.post("/api/v1/auth/login", json={"email": admin_email, "password": pw})
    assert login_admin.status_code == 200
    admin_token = login_admin.json()["access_token"]
    assert "refreshToken" in login_admin.cookies

    me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "ADMIN"
    assert isinstance(me.json()["id"], str)

    old_cookie = login_admin.cookies["refreshToken"]
    refreshed = client.post("/api/v1/auth/refresh", cookies={"refreshToken": old_cookie})
    assert refreshed.status_code == 200
    new_cookie = refreshed.cookies["refreshToken"]
    assert new_cookie != old_cookie
    assert (
        client.post("/api/v1/auth/refresh", cookies={"refreshToken": old_cookie}).status_code == 401
    )

    logout_res = client.post("/api/v1/auth/logout", cookies={"refreshToken": new_cookie})
    assert logout_res.status_code == 204
    assert (
        client.post("/api/v1/auth/refresh", cookies={"refreshToken": new_cookie}).status_code == 401
    )

    # 2. Catalog: admin creates category, public lists it
    cat_res = client.post(
        "/api/v1/categories",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": f"Category {suffix}", "description": "MySQL test category"},
    )
    assert cat_res.status_code == 201
    cat_id = cat_res.json()["id"]
    assert client.get("/api/v1/categories").status_code == 200

    # 3. Course: teacher creates & updates draft course
    teacher_token = client.post(
        "/api/v1/auth/login", json={"email": teacher_email, "password": pw}
    ).json()["access_token"]
    course_res = client.post(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {teacher_token}"},
        json={
            "title": f"Course {suffix}",
            "description": "Course desc",
            "category_id": cat_id,
            "price": "0.00",
        },
    )
    assert course_res.status_code == 201
    course_id = course_res.json()["id"]
    assert course_res.json()["price"] == "0.00"

    # 4. Lesson: teacher creates lesson with transcript, enrolled student sees redacted transcript
    lesson_res = client.post(
        f"/api/v1/courses/{course_id}/lessons",
        headers={"Authorization": f"Bearer {teacher_token}"},
        json={
            "title": "Lesson 1",
            "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "transcript_text": "Private teacher transcript",
        },
    )
    assert lesson_res.status_code == 201
    lesson_id = lesson_res.json()["id"]
    assert lesson_res.json()["transcript_text"] == "Private teacher transcript"

    student_token = client.post(
        "/api/v1/auth/login", json={"email": student_email, "password": pw}
    ).json()["access_token"]
    assert (
        client.get(
            f"/api/v1/lessons/{lesson_id}",
            headers={"Authorization": f"Bearer {student_token}"},
        ).status_code
        == 403
    )

    with session_scope(engine) as session:
        session.execute(
            text(
                "INSERT INTO course_members (course_id, student_id) "
                "VALUES (:course_id, :student_id)"
            ),
            {"course_id": int(course_id), "student_id": student_id},
        )

    student_lesson = client.get(
        f"/api/v1/lessons/{lesson_id}",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert student_lesson.status_code == 200
    assert student_lesson.json()["transcript_text"] is None
