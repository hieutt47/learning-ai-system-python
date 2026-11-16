"""Framework-independent lesson use cases; active identity is checked upstream."""

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from app.core.domain import Actor, DomainError, ResourceInUse, UnitOfWork, required_text


@dataclass(frozen=True)
class CourseAccess:
    id: int
    teacher_id: int
    is_published: bool = False
    archived_at: datetime | None = None
    enrolled: bool = False
    has_members: bool = False


class CourseAccessPort(Protocol):
    """Narrow course-access port, not another module's repository.

    Lock the course row for mutations; all adapters share the request transaction.
    Existing enrollment grants read access even after unpublish/archive.
    """

    def get(
        self, course_id: int, actor_id: int, *, for_update: bool = False
    ) -> CourseAccess | None: ...


@dataclass(frozen=True)
class Lesson:
    id: int | None
    course_id: int
    title: str
    video_url: str = ""
    transcript_text: str | None = None
    order_index: int = 1
    document_count: int = 0
    quiz_count: int = 0
    flashcard_count: int = 0
    generation_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    can_delete: bool = False
    delete_blocked_reason: str | None = None


class LessonRepository(Protocol):
    def list(self, course_id: int) -> list[Lesson]: ...
    def get(self, lesson_id: int, *, for_update: bool = False) -> Lesson | None: ...
    def next_order(self, course_id: int) -> int: ...
    def save(self, lesson: Lesson) -> Lesson: ...
    def dependencies(self, lesson_id: int) -> bool: ...
    def delete(self, lesson_id: int) -> None: ...


def validate_video_url(value: str | None) -> str:
    value = "" if value is None else value.strip()
    if not value:
        return ""
    try:
        uri = urlsplit(value)
        if len(value) > 2048 or uri.scheme not in {"http", "https"} or uri.username or uri.password:
            raise ValueError
        if uri.port not in {None, 80, 443} or any(c.isspace() for c in value):
            raise ValueError
        if uri.hostname == "youtu.be":
            video_id = uri.path.removeprefix("/")
        elif uri.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            if uri.path == "/watch":
                ids = parse_qs(uri.query).get("v", [])
                video_id = ids[0] if len(ids) == 1 else ""
            elif uri.path.startswith(("/embed/", "/shorts/")):
                video_id = uri.path.split("/", 2)[2]
            else:
                raise ValueError
        else:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError
    except ValueError:
        raise DomainError(
            "VALIDATION_ERROR", 400, "video_url must be a valid YouTube video URL."
        ) from None
    return value


class LessonService:
    def __init__(
        self,
        repository: LessonRepository,
        courses: CourseAccessPort,
        transaction: Callable[[], UnitOfWork],
    ):
        self.repository, self.courses, self.transaction = repository, courses, transaction

    def _course(self, actor: Actor, course_id: int, *, write: bool = False) -> CourseAccess:
        if write and actor.role not in {"TEACHER", "ADMIN"}:
            raise DomainError("FORBIDDEN", 403, "Teacher or administrator access required.")
        course = self.courses.get(course_id, actor.id, for_update=write)
        if course is None:
            raise DomainError("NOT_FOUND", 404, "Course not found.")
        manager = actor.role == "ADMIN" or (
            actor.role == "TEACHER" and course.teacher_id == actor.id
        )
        if not manager and not (not write and actor.role == "STUDENT" and course.enrolled):
            code = "ENROLLMENT_REQUIRED" if not write and actor.role == "STUDENT" else "FORBIDDEN"
            raise DomainError(code, 403, "Course access denied.")
        if write and (course.is_published or course.archived_at is not None):
            raise DomainError(
                "INVALID_STATE", 409, "Only draft lesson writes are supported in TASK008."
            )
        return course

    def _lesson(self, lesson_id: int, *, lock: bool = False) -> Lesson:
        lesson = self.repository.get(lesson_id, for_update=lock)
        if lesson is None:
            raise DomainError("NOT_FOUND", 404, "Lesson not found.")
        return lesson

    @staticmethod
    def _view(actor: Actor, course: CourseAccess, lesson: Lesson) -> Lesson:
        manager = actor.role == "ADMIN" or (
            actor.role == "TEACHER" and course.teacher_id == actor.id
        )
        blocked = (
            course.is_published
            or course.archived_at is not None
            or course.has_members
            or lesson.document_count
            or lesson.quiz_count
            or lesson.flashcard_count
            or lesson.generation_count
        )
        allowed = manager and not blocked
        return replace(
            lesson,
            transcript_text=lesson.transcript_text if manager else None,
            can_delete=allowed,
            delete_blocked_reason=None if allowed else "Only unused draft lessons can be deleted.",
        )

    def list(self, actor: Actor, course_id: int) -> list[Lesson]:
        course = self._course(actor, course_id)
        return [self._view(actor, course, item) for item in self.repository.list(course_id)]

    def get(self, actor: Actor, lesson_id: int) -> Lesson:
        lesson = self._lesson(lesson_id)
        return self._view(actor, self._course(actor, lesson.course_id), lesson)

    def create(
        self,
        actor: Actor,
        course_id: int,
        *,
        title: str,
        video_url: str | None = None,
        transcript_text: str | None = None,
    ) -> Lesson:
        with self.transaction() as uow:
            course = self._course(actor, course_id, write=True)
            lesson = Lesson(
                None,
                course_id,
                required_text(title, "title", 200),
                validate_video_url(video_url),
                self._transcript(transcript_text),
                self.repository.next_order(course_id),
            )
            result = self.repository.save(lesson)
            uow.commit()
            return self._view(actor, course, result)

    @staticmethod
    def _transcript(value: str | None) -> str | None:
        value = value.strip() if value is not None else None
        if value and len(value.encode("utf-8")) > 65535:
            raise DomainError(
                "VALIDATION_ERROR", 400, "transcript_text exceeds the existing TEXT storage limit."
            )
        return value or None

    def update(
        self,
        actor: Actor,
        lesson_id: int,
        *,
        title: str,
        video_url: str | None = None,
        transcript_text: str | None = None,
    ) -> Lesson:
        with self.transaction() as uow:
            course = self._course(actor, self._lesson(lesson_id).course_id, write=True)
            lesson = self._lesson(lesson_id, lock=True)
            result = self.repository.save(
                replace(
                    lesson,
                    title=required_text(title, "title", 200),
                    video_url=validate_video_url(video_url),
                    transcript_text=self._transcript(transcript_text),
                )
            )
            uow.commit()
            return self._view(actor, course, result)

    def delete(self, actor: Actor, lesson_id: int) -> None:
        try:
            with self.transaction() as uow:
                course = self._course(actor, self._lesson(lesson_id).course_id, write=True)
                lesson = self._lesson(lesson_id, lock=True)
                if not self._view(actor, course, lesson).can_delete or self.repository.dependencies(
                    lesson_id
                ):
                    raise ResourceInUse()
                self.repository.delete(lesson_id)
                uow.commit()
        except ResourceInUse as exc:
            raise DomainError(
                "INVALID_STATE", 409, "Only unused draft lessons can be deleted."
            ) from exc
