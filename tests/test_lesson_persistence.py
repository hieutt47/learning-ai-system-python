from datetime import UTC, datetime
from unittest.mock import Mock

from sqlalchemy.dialects import mysql

from app.modules.lesson.domain import Lesson
from app.modules.lesson.infrastructure import SqlCourseAccess, SqlLessonRepository


def result(*, row=None, rows=(), scalar=0, lastrowid=4):
    value = Mock(lastrowid=lastrowid)
    value.mappings.return_value.first.return_value = row
    value.mappings.return_value.__iter__ = Mock(return_value=iter(rows))
    value.first.return_value = row
    value.scalar_one.return_value = scalar
    return value


def row():
    return dict(
        id=4,
        course_id=1,
        title="Title",
        video_url="",
        transcript_text=None,
        order_index=1,
        document_count=0,
        quiz_count=0,
        flashcard_count=0,
        generation_count=0,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


def calls(session):
    return [(str(call.args[0]), call.args[1]) for call in session.execute.call_args_list]


def test_course_access_lock_current_membership_and_missing():
    session = Mock()
    session.execute.side_effect = [
        result(row=dict(id=1, teacher_id=2, is_published=0, archived_at=None)),
        result(row=(3,)),
        result(),
    ]
    port = SqlCourseAccess(session)
    course = port.get(1, 2, for_update=True)
    assert course.has_members is True
    assert all(query.endswith("FOR UPDATE") for query, _ in calls(session))
    assert port.get(9, 2) is None


def test_access_enrollment_query_parameters():
    session = Mock()
    session.execute.side_effect = [
        result(row=dict(id=1, teacher_id=2, is_published=1, archived_at=None)),
        result(scalar=1),
        result(scalar=1),
    ]
    course = SqlCourseAccess(session).get(1, 3)
    assert course.enrolled and course.is_published and course.has_members
    assert calls(session)[1][1] == {"id": 1, "actor": 3}


def test_lesson_list_current_get_next_order_utc():
    session = Mock()
    session.execute.side_effect = [
        result(rows=[row()]),
        result(row=row()),
        result(row=(6,)),
        result(),
    ]
    repo = SqlLessonRepository(session)
    assert repo.list(1)[0].created_at.tzinfo is UTC
    assert repo.get(4, for_update=True).id == 4
    assert repo.next_order(1) == 7
    assert repo.next_order(1) == 1
    assert "ORDER BY l.order_index ASC, l.id ASC" in calls(session)[0][0]
    assert calls(session)[1][0].endswith("FOR UPDATE")
    assert calls(session)[2][0].endswith("FOR UPDATE")


def test_save_parameterized_no_order_mutation_and_restrictive_delete():
    session = Mock()
    session.execute.side_effect = [
        result(),
        result(row=row()),
        result(),
        result(row=row()),
        result(),
        result(),
        result(),
        result(),
        result(),
    ]
    repo = SqlLessonRepository(session)
    saved = repo.save(Lesson(None, 1, "Title' SQL"))
    repo.save(saved)
    assert repo.dependencies(4) is False
    repo.delete(4)
    queries = calls(session)
    assert queries[0][1]["title"] == "Title' SQL" and "Title' SQL" not in queries[0][0]
    assert "order_index" not in queries[2][0]
    assert [query.split("FROM ")[1].split(" ")[0] for query, _ in queries[4:8]] == [
        "lesson_documents",
        "quizzes",
        "flashcards",
        "ai_generations",
    ]
    assert all(query.endswith("FOR UPDATE") for query, _ in queries[4:8])
    assert queries[-1] == ("DELETE FROM lessons WHERE id = :id", {"id": 4})
    for call in session.execute.call_args_list:
        call.args[0].compile(dialect=mysql.dialect())
