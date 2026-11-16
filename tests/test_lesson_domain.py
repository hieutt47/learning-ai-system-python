from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.domain import DomainError, ResourceInUse
from app.modules.lesson.domain import CourseAccess, Lesson, LessonService, validate_video_url

OWNER = SimpleNamespace(id=2, role="TEACHER")
ADMIN = SimpleNamespace(id=9, role="ADMIN")
STUDENT = SimpleNamespace(id=3, role="STUDENT")
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def setup():
    repo, courses, uow = Mock(), Mock(), Mock()
    uow.__enter__ = Mock(return_value=uow)
    uow.__exit__ = Mock(return_value=False)
    courses.get.return_value = CourseAccess(1, 2)
    repo.get.return_value = Lesson(
        4, 1, "Lesson", transcript_text="private", created_at=NOW, updated_at=NOW
    )
    repo.list.return_value = [repo.get.return_value]
    repo.next_order.return_value = 6
    repo.dependencies.return_value = False
    repo.save.side_effect = lambda lesson: replace(lesson, id=4, created_at=NOW, updated_at=NOW)
    return LessonService(repo, courses, lambda: uow), repo, courses, uow


@pytest.mark.parametrize("actor", [OWNER, ADMIN])
def test_manager_create_update_and_delete(setup, actor):
    service, repo, courses, uow = setup
    lesson = service.create(actor, 1, title="  New  ", transcript_text="  text  ")
    assert (lesson.title, lesson.video_url, lesson.transcript_text, lesson.order_index) == (
        "New",
        "",
        "text",
        6,
    )
    courses.get.assert_called_with(1, actor.id, for_update=True)
    updated = service.update(actor, 4, title="Replace")
    assert updated.transcript_text is None and updated.video_url == ""
    service.delete(actor, 4)
    repo.delete.assert_called_once_with(4)
    assert uow.commit.call_count == 3


@pytest.mark.parametrize(
    "actor,code",
    [(STUDENT, "ENROLLMENT_REQUIRED"), (SimpleNamespace(id=5, role="TEACHER"), "FORBIDDEN")],
)
def test_read_denies_unenrolled_and_other_teacher(setup, actor, code):
    service, _, _, _ = setup
    for operation in (lambda: service.get(actor, 4), lambda: service.list(actor, 1)):
        with pytest.raises(DomainError) as error:
            operation()
        assert error.value.code == code


@pytest.mark.parametrize("archived,published", [(None, False), (NOW, False), (None, True)])
def test_enrolled_student_redacted_but_manager_private(setup, archived, published):
    service, repo, courses, _ = setup
    courses.get.return_value = CourseAccess(
        1, 2, published, archived, enrolled=True, has_members=True
    )
    assert service.get(STUDENT, 4).transcript_text is None
    assert service.list(STUDENT, 1)[0].transcript_text is None
    assert service.get(OWNER, 4).transcript_text == "private"
    assert repo.get.return_value.transcript_text == "private"
    assert service.get(OWNER, 4).can_delete is False


@pytest.mark.parametrize("actor", [STUDENT, SimpleNamespace(id=5, role="TEACHER")])
def test_mutations_forbidden(setup, actor):
    service, repo, _, uow = setup
    for operation in (
        lambda: service.create(actor, 1, title="x"),
        lambda: service.update(actor, 4, title="x"),
        lambda: service.delete(actor, 4),
    ):
        with pytest.raises(DomainError, match="access|denied") as error:
            operation()
        assert error.value.status == 403
    repo.save.assert_not_called()
    uow.commit.assert_not_called()


@pytest.mark.parametrize("course", [CourseAccess(1, 2, True), CourseAccess(1, 2, archived_at=NOW)])
def test_only_draft_writes(setup, course):
    service, _, courses, uow = setup
    courses.get.return_value = course
    for operation in (
        lambda: service.create(OWNER, 1, title="x"),
        lambda: service.update(OWNER, 4, title="x"),
        lambda: service.delete(OWNER, 4),
    ):
        with pytest.raises(DomainError) as error:
            operation()
        assert error.value.status == 409
    uow.commit.assert_not_called()


@pytest.mark.parametrize(
    "field", ["document_count", "quiz_count", "flashcard_count", "generation_count"]
)
def test_dependencies_block_delete(setup, field):
    service, repo, _, uow = setup
    repo.get.return_value = replace(repo.get.return_value, **{field: 1})
    with pytest.raises(DomainError) as error:
        service.delete(OWNER, 4)
    assert error.value.status == 409
    repo.delete.assert_not_called()
    uow.commit.assert_not_called()


def test_member_current_dependency_and_restrict_race(setup):
    service, repo, courses, _ = setup
    courses.get.return_value = CourseAccess(1, 2, has_members=True)
    with pytest.raises(DomainError):
        service.delete(OWNER, 4)
    courses.get.return_value = CourseAccess(1, 2)
    repo.dependencies.return_value = True
    with pytest.raises(DomainError):
        service.delete(OWNER, 4)
    repo.dependencies.return_value = False
    repo.delete.side_effect = ResourceInUse
    with pytest.raises(DomainError) as error:
        service.delete(OWNER, 4)
    assert error.value.status == 409


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://youtu.be/dQw4w9WgXcQ?t=4",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/shorts/dQw4w9WgXcQ",
        "https://youtube.com/embed/dQw4w9WgXcQ",
    ],
)
def test_youtube_supported(url):
    assert validate_video_url(url) == (url or "")


@pytest.mark.parametrize(
    "url",
    [
        "<iframe src='x'>",
        "javascript:alert(1)",
        "https://youtube.com.evil/watch?v=dQw4w9WgXcQ",
        "https://evil@youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/no",
        "https://youtu.be/dQw4w9WgXcQ/extra",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&v=other",
        "https://youtube.com:bad/watch?v=dQw4w9WgXcQ",
    ],
)
def test_youtube_rejects_arbitrary_or_malformed(url):
    with pytest.raises(DomainError):
        validate_video_url(url)


def test_missing_and_invalid_content_rollback(setup):
    service, repo, courses, uow = setup
    with pytest.raises(DomainError):
        service.create(OWNER, 1, title=" ")
    with pytest.raises(DomainError):
        service.create(OWNER, 1, title="x", transcript_text="汉" * 22000)
    courses.get.return_value = None
    with pytest.raises(DomainError) as error:
        service.list(OWNER, 1)
    assert error.value.status == 404
    repo.get.return_value = None
    with pytest.raises(DomainError) as error:
        service.get(OWNER, 4)
    assert error.value.status == 404
    uow.commit.assert_not_called()
