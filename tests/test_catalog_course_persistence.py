from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pymysql.err import IntegrityError as DriverIntegrityError
from sqlalchemy.exc import IntegrityError

from app.core.catalog_course_dependencies import SqlUnitOfWork, get_session, utc
from app.core.domain import ResourceInUse
from app.modules.catalog.domain import Category, DuplicateName
from app.modules.catalog.infrastructure import SqlCategoryRepository
from app.modules.course.domain import Course
from app.modules.course.infrastructure import SqlCourseCategoryLookup, SqlCourseRepository


def result(*, row=None, rows=(), scalar=0, lastrowid=2):
    value = Mock(lastrowid=lastrowid)
    value.mappings.return_value.first.return_value = row
    value.mappings.return_value.__iter__ = Mock(return_value=iter(rows))
    value.scalar_one.return_value = scalar
    value.first.return_value = row
    return value


def sql_calls(session):
    return [
        (str(call.args[0]), call.args[1] if len(call.args) > 1 else {})
        for call in session.execute.call_args_list
    ]


def category_row():
    return dict(
        id=2,
        name="Science",
        description=None,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


def course_row():
    return dict(
        id=2,
        title="Title",
        description="Description",
        category_id=1,
        teacher_id=2,
        price=Decimal("1.25"),
        is_published=0,
        archived_at=None,
        thumbnail_url=None,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
        category_name="Science",
        teacher_name="Teacher",
        lesson_count=4,
        total_students=5,
    )


def test_category_sql_order_filter_lock_and_utc():
    session = Mock()
    session.execute.side_effect = [
        result(rows=[category_row()]),
        result(scalar=1),
        result(row=category_row()),
        result(scalar=1),
    ]
    repo = SqlCategoryRepository(session)
    items, total = repo.list(2, 10)
    assert total == 1 and items[0].created_at.tzinfo is UTC
    assert repo.get(2, for_update=True).id == 2
    assert repo.name_exists("name' SQL", excluding_id=2)
    calls = sql_calls(session)
    assert "ORDER BY created_at DESC, id DESC" in calls[0][0]
    assert calls[0][1] == {"limit": 10, "offset": 10}
    assert calls[2] == ("SELECT * FROM categories WHERE id = :id FOR UPDATE", {"id": 2})
    assert calls[3][1] == {"name": "name' SQL", "excluded": 2}
    assert "name' SQL" not in calls[3][0]


def test_category_insert_uses_generated_id_and_database_timestamps():
    session = Mock()
    session.execute.side_effect = [result(lastrowid=2), result(row=category_row())]
    saved = SqlCategoryRepository(session).save(Category(None, "Science"))
    assert saved.id == 2 and saved.updated_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert sql_calls(session)[1][1] == {"id": 2}


def test_course_owner_filter_projection_current_read_and_category_port():
    session = Mock()
    session.execute.side_effect = [
        result(rows=[course_row()]),
        result(scalar=1),
        result(row=course_row()),
        result(row={"id": 1}),
    ]
    repo = SqlCourseRepository(session)
    items, total = repo.list(2, 10, teacher_id=2)
    assert total == 1 and items[0].lesson_count == 4 and items[0].total_students == 5
    assert repo.get(2, for_update=True).created_at.tzinfo is UTC
    assert SqlCourseCategoryLookup(session).exists(1, for_update=True)
    calls = sql_calls(session)
    assert all("c.teacher_id = :teacher_id" in calls[i][0] for i in (0, 1))
    assert calls[0][1] == {"teacher_id": 2, "limit": 10, "offset": 10}
    assert "COUNT(*) FROM lessons" in calls[0][0] and "COUNT(*) FROM course_members" in calls[0][0]
    assert "JOIN categories" in calls[0][0] and "JOIN users" in calls[0][0]
    assert calls[2][0].endswith("FOR UPDATE")
    assert calls[3] == ("SELECT id FROM categories WHERE id = :id FOR UPDATE", {"id": 1})


def test_course_insert_update_and_dependency_queries_never_cascade():
    session = Mock()
    session.execute.side_effect = [
        result(),
        result(row=course_row()),
        result(),
        result(row=course_row()),
        result(scalar=1),
        result(scalar=1),
        result(),
    ]
    repo = SqlCourseRepository(session)
    saved = repo.save(Course(None, "Title", "Description", 1, 2, Decimal("1.25")))
    repo.save(saved)
    assert repo.dependencies(2) == ("lessons", "course_members")
    repo.delete(2)
    calls = sql_calls(session)
    assert calls[0][1]["price"] == Decimal("1.25") and calls[0][1]["teacher_id"] == 2
    assert "teacher_id" not in calls[2][1]
    assert calls[-1] == ("DELETE FROM courses WHERE id = :id", {"id": 2})
    assert not any("CASCADE" in query for query, _ in calls)


@pytest.mark.parametrize(
    "code,duplicate,expected",
    [
        (1062, DuplicateName, DuplicateName),
        (1451, None, ResourceInUse),
        (9999, None, IntegrityError),
    ],
)
@pytest.mark.parametrize("at_commit", [False, True])
def test_constraint_translation_statement_and_commit_rollback(code, duplicate, expected, at_commit):
    session = Mock()
    error = IntegrityError("hidden SQL", {}, DriverIntegrityError(code, "hidden driver"))
    if at_commit:
        session.commit.side_effect = error
    with pytest.raises(expected):
        with SqlUnitOfWork(session, duplicate_error=duplicate) as tx:
            if not at_commit:
                raise error
            tx.commit()
    session.rollback.assert_called_once()
    session.close.assert_not_called()


def test_uow_commit_no_close_and_uncommitted_rollback():
    session = Mock()
    with SqlUnitOfWork(session) as tx:
        tx.commit()
    session.commit.assert_called_once()
    session.rollback.assert_not_called()
    with SqlUnitOfWork(session):
        pass
    session.rollback.assert_called_once()
    session.close.assert_not_called()


def test_request_session_always_rolls_back_and_closes_without_auth(monkeypatch):
    from threading import Lock

    import app.core.catalog_course_dependencies as deps

    session, engine = Mock(), Mock()
    monkeypatch.setattr(deps, "Session", Mock(return_value=session))
    state = SimpleNamespace(identity_engine=engine, identity_engine_lock=Lock(), auth_settings=None)
    generator = get_session(SimpleNamespace(app=SimpleNamespace(state=state)))
    assert next(generator) is session
    generator.close()
    session.rollback.assert_called_once()
    session.close.assert_called_once()
    assert utc(datetime(2026, 1, 1)).tzinfo is UTC
    assert utc(None) is None
