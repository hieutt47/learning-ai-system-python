"""Real MySQL tests for learning attempts, grading, V4 constraint, and concurrency races."""

import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests import test_mysql_modules as _mysql_mod

mysql_app_env = _mysql_mod.mysql_app_env
mysql_client = _mysql_mod.mysql_client
_seed_user = _mysql_mod._seed_user

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
)


@pytest.mark.mysql
def test_mysql_learning_concurrency_race_grading_closed_submit_and_isolation(
    mysql_client, tmp_path, monkeypatch
):
    client, engine = mysql_client
    monkeypatch.setenv("APP_STORAGE_LOCATION", str(tmp_path))

    suffix = secrets.token_hex(4)
    admin_email = f"lrn.admin.{suffix}@example.invalid"
    teacher_email = f"lrn.teacher.{suffix}@example.invalid"
    s1_email = f"lrn.s1.{suffix}@example.invalid"
    s2_email = f"lrn.s2.{suffix}@example.invalid"
    pw = "ValidPassw0rd!"

    _seed_user(engine, email=admin_email, password=pw, role="ADMIN", full_name="Admin")
    _seed_user(engine, email=teacher_email, password=pw, role="TEACHER", full_name="Teacher")
    _seed_user(engine, email=s1_email, password=pw, role="STUDENT", full_name="S1")
    _seed_user(engine, email=s2_email, password=pw, role="STUDENT", full_name="S2")

    admin_tok = client.post(
        "/api/v1/auth/login", json={"email": admin_email, "password": pw}
    ).json()["access_token"]
    teacher_tok = client.post(
        "/api/v1/auth/login", json={"email": teacher_email, "password": pw}
    ).json()["access_token"]
    s1_tok = client.post("/api/v1/auth/login", json={"email": s1_email, "password": pw}).json()[
        "access_token"
    ]
    s2_tok = client.post("/api/v1/auth/login", json={"email": s2_email, "password": pw}).json()[
        "access_token"
    ]

    cat_id = client.post(
        "/api/v1/categories",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={"name": f"LearnCat {suffix}"},
    ).json()["id"]
    course_id = client.post(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={
            "title": f"LearnCourse {suffix}",
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
        f"/api/v1/courses/{course_id}/enroll", headers={"Authorization": f"Bearer {s1_tok}"}
    )
    client.post(
        f"/api/v1/courses/{course_id}/enroll", headers={"Authorization": f"Bearer {s2_tok}"}
    )

    quiz_id = client.post(
        f"/api/v1/lessons/{lesson_id}/quizzes",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={"title": "Graded Quiz"},
    ).json()["id"]
    q1 = client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={
            "content": "Q1",
            "explanation": "Exp1",
            "order_index": 0,
            "options": [
                {"content": "Right", "is_correct": True, "order_index": 0},
                {"content": "Wrong", "is_correct": False, "order_index": 1},
            ],
        },
    ).json()
    q2 = client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={
            "content": "Q2",
            "explanation": "Exp2",
            "order_index": 1,
            "options": [
                {"content": "Right2", "is_correct": True, "order_index": 0},
                {"content": "Wrong2", "is_correct": False, "order_index": 1},
            ],
        },
    ).json()
    client.patch(
        f"/api/v1/quizzes/{quiz_id}/publication",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={"status": "PUBLISHED"},
    )

    # Concurrent start race for s1 -> only 1 IN_PROGRESS attempt created, all get same id
    def _do_start():
        return client.post(
            f"/api/v1/quizzes/{quiz_id}/attempts",
            headers={"Authorization": f"Bearer {s1_tok}"},
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: _do_start(), range(4)))
    assert all(r.status_code in {200, 201} for r in results)
    ids = {r.json()["id"] for r in results}
    assert len(ids) == 1
    attempt_id = ids.pop()

    # Close quiz: s2 cannot start (409), s1's already-started attempt can still submit
    client.patch(
        f"/api/v1/quizzes/{quiz_id}/publication",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={"status": "CLOSED"},
    )
    assert (
        client.post(
            f"/api/v1/quizzes/{quiz_id}/attempts",
            headers={"Authorization": f"Bearer {s2_tok}"},
        ).status_code
        == 409
    )

    # Concurrent submit race with different answers -> atomic single grading, consistent result
    q1_right = q1["options"][0]["id"]
    q2_right = q2["options"][0]["id"]
    q2_wrong = q2["options"][1]["id"]
    payload_half = {
        "answers": [
            {"question_id": q1["id"], "selected_option_id": q1_right},
            {"question_id": q2["id"], "selected_option_id": q2_wrong},
        ]
    }
    payload_full = {
        "answers": [
            {"question_id": q1["id"], "selected_option_id": q1_right},
            {"question_id": q2["id"], "selected_option_id": q2_right},
        ]
    }

    def _do_submit(payload):
        return client.post(
            f"/api/v1/attempts/{attempt_id}/submit",
            headers={"Authorization": f"Bearer {s1_tok}"},
            json=payload,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        sub_results = list(pool.map(_do_submit, [payload_half, payload_full]))
    assert all(r.status_code == 200 for r in sub_results)
    scores = {r.json()["score"] for r in sub_results}
    assert len(scores) == 1
    winning_score = scores.pop()

    # Subsequent retry with opposite payload must still return winning_score
    retry = _do_submit(payload_full if winning_score == 50.0 else payload_half)
    assert retry.status_code == 200
    assert retry.json()["score"] == winning_score

    # Account isolation: s2 sees 403 on s1's attempt and 0 in own history
    assert (
        client.get(
            f"/api/v1/attempts/{attempt_id}",
            headers={"Authorization": f"Bearer {s2_tok}"},
        ).status_code
        == 403
    )
    s2_hist = client.get(
        "/api/v1/users/me/attempts",
        headers={"Authorization": f"Bearer {s2_tok}"},
    )
    assert s2_hist.status_code == 200
    assert s2_hist.json()["total"] == 0

    s1_hist = client.get(
        "/api/v1/users/me/attempts",
        headers={"Authorization": f"Bearer {s1_tok}"},
        params={"quiz_id": quiz_id, "course_id": course_id},
    )
    assert s1_hist.status_code == 200
    assert s1_hist.json()["total"] == 1
    assert s1_hist.json()["items"][0]["score"] == winning_score
