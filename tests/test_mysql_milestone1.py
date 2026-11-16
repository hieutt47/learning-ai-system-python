"""Real MySQL E2E milestone: Teacher publish -> Student marketplace -> enroll -> read."""

import secrets

import pytest

from tests import test_mysql_modules as _mysql_mod

mysql_app_env = _mysql_mod.mysql_app_env
mysql_client = _mysql_mod.mysql_client
_seed_user = _mysql_mod._seed_user

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
)


@pytest.mark.mysql
def test_mysql_milestone_publish_marketplace_enroll_read_and_archive(
    mysql_client, tmp_path, monkeypatch
):
    client, engine = mysql_client
    monkeypatch.setenv("APP_STORAGE_LOCATION", str(tmp_path))

    suffix = secrets.token_hex(4)
    admin_email = f"m1.admin.{suffix}@example.invalid"
    teacher_email = f"m1.teacher.{suffix}@example.invalid"
    s1_email = f"m1.s1.{suffix}@example.invalid"
    s2_email = f"m1.s2.{suffix}@example.invalid"
    pw = "ValidPassw0rd!"

    _seed_user(engine, email=admin_email, password=pw, role="ADMIN", full_name="Admin")
    _seed_user(engine, email=teacher_email, password=pw, role="TEACHER", full_name="Teacher")
    _seed_user(engine, email=s1_email, password=pw, role="STUDENT", full_name="Student 1")
    _seed_user(engine, email=s2_email, password=pw, role="STUDENT", full_name="Student 2")

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
        json={"name": f"MilestoneCat {suffix}"},
    ).json()["id"]

    # Teacher creates draft course
    course_res = client.post(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={
            "title": f"Milestone Course {suffix}",
            "description": "End-to-end milestone course",
            "category_id": cat_id,
            "price": "0.00",
        },
    )
    assert course_res.status_code == 201
    course_id = course_res.json()["id"]

    # Publish fails before thumbnail and lesson
    assert (
        client.patch(
            f"/api/v1/courses/{course_id}/publication",
            headers={"Authorization": f"Bearer {teacher_tok}"},
            json={"is_published": True},
        ).status_code
        == 409
    )

    # Teacher uploads thumbnail and serves it
    thumb_res = client.post(
        f"/api/v1/courses/{course_id}/thumbnail",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        files={"file": ("cover.png", PNG_BYTES, "image/png")},
    )
    assert thumb_res.status_code == 200
    thumb_url = thumb_res.json()["thumbnail_url"]
    assert client.get(thumb_url).status_code == 200

    # Teacher adds lesson with private transcript
    lesson_res = client.post(
        f"/api/v1/courses/{course_id}/lessons",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={
            "title": "Lesson 1",
            "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "transcript_text": "Owner-only private transcript",
        },
    )
    assert lesson_res.status_code == 201
    lesson_id = lesson_res.json()["id"]

    # Teacher publishes course
    pub_res = client.patch(
        f"/api/v1/courses/{course_id}/publication",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={"is_published": True},
    )
    assert pub_res.status_code == 200
    assert pub_res.json()["is_published"] is True

    # Student explores marketplace (search & public outline without private video/transcript)
    market_list = client.get("/api/v1/courses", params={"q": suffix, "category_id": cat_id})
    assert market_list.status_code == 200
    assert market_list.json()["total"] == 1
    public_detail = client.get(f"/api/v1/courses/{course_id}")
    assert public_detail.status_code == 200
    assert public_detail.json()["outline"] == [
        {"id": lesson_id, "title": "Lesson 1", "order_index": 1}
    ]
    assert "video_url" not in public_detail.json()["outline"][0]
    assert "transcript_text" not in public_detail.json()["outline"][0]

    # Student 1 enrolls and reads lesson (transcript hidden from student, visible to owner)
    enroll_res = client.post(
        f"/api/v1/courses/{course_id}/enroll",
        headers={"Authorization": f"Bearer {s1_tok}"},
    )
    assert enroll_res.status_code == 201

    owner_lesson = client.get(
        f"/api/v1/lessons/{lesson_id}", headers={"Authorization": f"Bearer {teacher_tok}"}
    )
    assert owner_lesson.status_code == 200
    assert owner_lesson.json()["transcript_text"] == "Owner-only private transcript"

    s1_lesson = client.get(
        f"/api/v1/lessons/{lesson_id}", headers={"Authorization": f"Bearer {s1_tok}"}
    )
    assert s1_lesson.status_code == 200
    assert s1_lesson.json()["transcript_text"] is None

    # Archive blocks new student (s2) but preserves existing student (s1) access
    arch_res = client.patch(
        f"/api/v1/courses/{course_id}/archive",
        headers={"Authorization": f"Bearer {teacher_tok}"},
        json={"archived": True},
    )
    assert arch_res.status_code == 200
    assert arch_res.json()["is_published"] is False
    assert arch_res.json()["archived_at"] is not None

    assert client.get(f"/api/v1/courses/{course_id}").status_code == 404
    assert (
        client.post(
            f"/api/v1/courses/{course_id}/enroll",
            headers={"Authorization": f"Bearer {s2_tok}"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/lessons/{lesson_id}", headers={"Authorization": f"Bearer {s1_tok}"}
        ).status_code
        == 200
    )
