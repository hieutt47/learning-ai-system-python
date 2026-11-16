"""Real MySQL tests for quiz authoring, V9 draft idempotency, rollback, and immutability."""

import secrets
import uuid

import pytest

from tests import test_mysql_modules as _mysql_mod

mysql_app_env = _mysql_mod.mysql_app_env
mysql_client = _mysql_mod.mysql_client
_seed_user = _mysql_mod._seed_user

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
)


@pytest.mark.mysql
def test_mysql_quiz_authoring_draft_idempotency_and_immutability(
    mysql_client, tmp_path, monkeypatch
):
    client, engine = mysql_client
    monkeypatch.setenv("APP_STORAGE_LOCATION", str(tmp_path))

    suffix = secrets.token_hex(4)
    admin_email = f"qa.admin.{suffix}@example.invalid"
    teacher_email = f"qa.teacher.{suffix}@example.invalid"
    student_email = f"qa.student.{suffix}@example.invalid"
    pw = "ValidPassw0rd!"

    _seed_user(engine, email=admin_email, password=pw, role="ADMIN", full_name="Admin")
    _seed_user(engine, email=teacher_email, password=pw, role="TEACHER", full_name="Teacher")
    _seed_user(engine, email=student_email, password=pw, role="STUDENT", full_name="Student")

    admin_tok = client.post(
        "/api/v1/auth/login", json={"email": admin_email, "password": pw}
    ).json()["access_token"]
    teacher_tok = client.post(
        "/api/v1/auth/login", json={"email": teacher_email, "password": pw}
    ).json()["access_token"]
    student_tok = client.post(
        "/api/v1/auth/login", json={"email": student_email, "password": pw}
    ).json()["access_token"]

    cat_id = client.post(
        "/api/v1/categories",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={"name": f"QuizCat {suffix}"},
    ).json()["id"]
    course_id = client.post(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={
            "title": f"QuizCourse {suffix}",
            "description": "Desc",
            "category_id": cat_id,
            "price": "0.00",
        },
    ).json()["id"]
    client.post(
        f"/api/v1/courses/{course_id}/thumbnail",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        files={"file": ("cover.png", PNG_BYTES, "image/png")},
    )
    lesson_id = client.post(
        f"/api/v1/courses/{course_id}/lessons",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={"title": "L1", "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    ).json()["id"]
    client.patch(
        f"/api/v1/courses/{course_id}/publication",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={"is_published": True},
    )
    client.post(
        f"/api/v1/courses/{course_id}/enroll",
        headers={"Authorization": f"Bearer {student_tok}"},
    )

    # Create draft quiz
    q_res = client.post(
        f"/api/v1/lessons/{lesson_id}/quizzes",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={"title": "Draft Quiz"},
    )
    assert q_res.status_code == 201
    quiz_id = q_res.json()["id"]

    # Atomic draft save + V9 receipt replay
    req_id = str(uuid.uuid4())
    draft_body = {
        "request_id": req_id,
        "title": "MySQL Quiz 1",
        "questions": [
            {
                "content": "What is 1+1?",
                "explanation": "Hidden from student",
                "order_index": 0,
                "options": [
                    {"content": "2", "is_correct": True, "order_index": 0},
                    {"content": "3", "is_correct": False, "order_index": 1},
                ],
            }
        ],
    }
    s1 = client.put(
        f"/api/v1/quizzes/{quiz_id}/draft",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json=draft_body,
    )
    assert s1.status_code == 200
    s2 = client.put(
        f"/api/v1/quizzes/{quiz_id}/draft",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json=draft_body,
    )
    assert s2.status_code == 200
    assert s2.json() == s1.json()

    # Rollback on invalid draft save (missing correct option in question 2) preserves question 1
    bad_draft = {
        "request_id": str(uuid.uuid4()),
        "title": "Should Rollback",
        "questions": [
            {
                "content": "Valid Q1",
                "explanation": None,
                "order_index": 0,
                "options": [
                    {"content": "A", "is_correct": True, "order_index": 0},
                    {"content": "B", "is_correct": False, "order_index": 1},
                ],
            },
            {
                "content": "Invalid Q2",
                "explanation": None,
                "order_index": 1,
                "options": [
                    {"content": "X", "is_correct": False, "order_index": 0},
                    {"content": "Y", "is_correct": False, "order_index": 1},
                ],
            },
        ],
    }
    assert (
        client.put(
            f"/api/v1/quizzes/{quiz_id}/draft",
            headers={"Authorization": f"Bearer {teacher_tok}"},
            json=bad_draft,
        ).status_code
        == 400
    )
    still_original = client.get(
        f"/api/v1/quizzes/{quiz_id}",
        headers={"Authorization": f"Bearer {teacher_tok}"},
    ).json()
    assert still_original["title"] == "MySQL Quiz 1"

    # Publish & verify student redaction & immutability
    pub = client.patch(
        f"/api/v1/quizzes/{quiz_id}/publication",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={"status": "PUBLISHED"},
    )
    assert pub.status_code == 200
    assert (
        client.patch(
            f"/api/v1/quizzes/{quiz_id}",
            headers={"Authorization": f"Bearer {teacher_tok}"},
            json={"title": "Mutate"},
        ).status_code
        == 409
    )

    stu_view = client.get(
        f"/api/v1/quizzes/{quiz_id}",
        headers={"Authorization": f"Bearer {student_tok}"},
    )
    assert stu_view.status_code == 200
    assert "is_correct" not in stu_view.text
    assert "Hidden from student" not in stu_view.text
