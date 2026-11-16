"""Opt-in real MySQL integration tests for enrollment."""

import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import session_scope
from tests import test_mysql_modules as _mysql_mod

mysql_app_env = _mysql_mod.mysql_app_env
mysql_client = _mysql_mod.mysql_client
_seed_user = _mysql_mod._seed_user


@pytest.mark.mysql
def test_mysql_enrollment_lifecycle_retry_archive_and_unique(mysql_client):
    client, engine = mysql_client
    suffix = secrets.token_hex(4)
    admin_email = f"enroll.admin.{suffix}@example.invalid"
    teacher_email = f"enroll.teacher.{suffix}@example.invalid"
    student1_email = f"enroll.s1.{suffix}@example.invalid"
    student2_email = f"enroll.s2.{suffix}@example.invalid"
    pw = "ValidPassw0rd!"

    _seed_user(engine, email=admin_email, password=pw, role="ADMIN", full_name="Admin")
    _seed_user(engine, email=teacher_email, password=pw, role="TEACHER", full_name="Teacher")
    s1_id = _seed_user(engine, email=student1_email, password=pw, role="STUDENT", full_name="S1")
    _seed_user(engine, email=student2_email, password=pw, role="STUDENT", full_name="S2")

    admin_tok = client.post(
        "/api/v1/auth/login", json={"email": admin_email, "password": pw}
    ).json()["access_token"]
    teacher_tok = client.post(
        "/api/v1/auth/login", json={"email": teacher_email, "password": pw}
    ).json()["access_token"]
    s1_tok = client.post(
        "/api/v1/auth/login", json={"email": student1_email, "password": pw}
    ).json()["access_token"]
    s2_tok = client.post(
        "/api/v1/auth/login", json={"email": student2_email, "password": pw}
    ).json()["access_token"]

    cat_id = client.post(
        "/api/v1/categories",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={"name": f"EnrollCat {suffix}"},
    ).json()["id"]
    course_id = client.post(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={
            "title": f"EnrollCourse {suffix}",
            "description": "Desc",
            "category_id": cat_id,
            "price": "0.00",
        },
    ).json()["id"]
    lesson_id = client.post(
        f"/api/v1/courses/{course_id}/lessons",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={
            "title": "Lesson 1",
            "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "transcript_text": "Hidden transcript",
        },
    ).json()["id"]

    # Draft cannot be enrolled
    assert (
        client.post(
            f"/api/v1/courses/{course_id}/enroll",
            headers={"Authorization": f"Bearer {s1_tok}"},
        ).status_code
        == 404
    )

    # Publish in DB to test enrollment before publication endpoints exist
    with session_scope(engine) as session:
        session.execute(
            text("UPDATE courses SET is_published = TRUE WHERE id = :id"),
            {"id": int(course_id)},
        )

    # First enroll -> 201, retry -> 200
    r_first = client.post(
        f"/api/v1/courses/{course_id}/enroll",
        headers={"Authorization": f"Bearer {s1_tok}"},
    )
    assert r_first.status_code == 201
    r_retry = client.post(
        f"/api/v1/courses/{course_id}/enroll",
        headers={"Authorization": f"Bearer {s1_tok}"},
    )
    assert r_retry.status_code == 200
    assert r_retry.json() == r_first.json()

    # DB UNIQUE constraint protects against duplicate insert
    with pytest.raises(IntegrityError), session_scope(engine) as session:
        session.execute(
            text("INSERT INTO course_members (course_id, student_id) VALUES (:c, :s)"),
            {"c": int(course_id), "s": s1_id},
        )

    # Archive blocks new student (s2 -> 404) but s1 retry still returns 200 and keeps access
    with session_scope(engine) as session:
        session.execute(
            text("UPDATE courses SET archived_at = UTC_TIMESTAMP(6) WHERE id = :id"),
            {"id": int(course_id)},
        )
    assert (
        client.post(
            f"/api/v1/courses/{course_id}/enroll",
            headers={"Authorization": f"Bearer {s2_tok}"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/courses/{course_id}/enroll",
            headers={"Authorization": f"Bearer {s1_tok}"},
        ).status_code
        == 200
    )

    mine = client.get("/api/v1/users/me/courses", headers={"Authorization": f"Bearer {s1_tok}"})
    assert mine.status_code == 200
    assert any(
        item["id"] == course_id and item["archived_at"] is not None for item in mine.json()["items"]
    )

    lesson_s1 = client.get(
        f"/api/v1/lessons/{lesson_id}", headers={"Authorization": f"Bearer {s1_tok}"}
    )
    assert lesson_s1.status_code == 200
    assert lesson_s1.json()["transcript_text"] is None

    members = client.get(
        f"/api/v1/courses/{course_id}/members",
        headers={"Authorization": f"Bearer {teacher_tok}"},
    )
    assert members.status_code == 200
    assert members.json()["total"] == 1
    assert members.json()["items"][0]["student_id"] == str(s1_id)
