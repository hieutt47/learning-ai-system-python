"""Enrollment is serialized on the course aggregate, including duplicate requests."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.core.domain import Actor, DomainError, Page, UnitOfWork, pagination


@dataclass(frozen=True)
class CourseAccess:
    id: int
    teacher_id: int
    is_published: bool
    archived_at: datetime | None
    price: Decimal


@dataclass(frozen=True)
class Membership:
    course_id: int
    student_id: int
    enrolled_at: datetime


class EnrollmentRepository(Protocol):
    def course(self, course_id: int, *, lock: bool = False) -> CourseAccess | None: ...
    def membership(self, course_id: int, student_id: int) -> Membership | None: ...
    def add(self, course_id: int, student_id: int) -> Membership: ...
    def mine(self, student_id: int, page: int, page_size: int) -> tuple[list[dict], int]: ...
    def members(self, course_id: int, page: int, page_size: int) -> tuple[list[dict], int]: ...


class EnrollmentService:
    def __init__(self, repository: EnrollmentRepository, transaction: Callable[[], UnitOfWork]):
        self.repository, self.transaction = repository, transaction

    @staticmethod
    def _student(actor: Actor) -> None:
        if actor.role != "STUDENT":
            raise DomainError("FORBIDDEN", 403, "Only students can enroll or list their courses.")

    def enroll(self, actor: Actor, course_id: int) -> tuple[Membership, bool]:
        self._student(actor)
        with self.transaction() as uow:
            course = self.repository.course(course_id, lock=True)
            if course is None:
                raise DomainError("NOT_FOUND", 404, "Course not found.")
            existing = self.repository.membership(course_id, actor.id)
            if existing is not None:
                # A retry must not lose its successful result after archive/unpublish.
                return existing, False
            if not course.is_published or course.archived_at is not None:
                raise DomainError("NOT_FOUND", 404, "Course not found.")
            if course.price != Decimal("0"):
                raise DomainError("COURSE_NOT_FREE", 409, "Only free courses can be enrolled.")
            membership = self.repository.add(course_id, actor.id)
            uow.commit()
            return membership, True

    def mine(self, actor: Actor, page: int = 1, page_size: int = 20) -> Page[dict]:
        self._student(actor)
        pagination(page, page_size)
        items, total = self.repository.mine(actor.id, page, page_size)
        return Page(items, page, page_size, total)

    def members(
        self, actor: Actor, course_id: int, page: int = 1, page_size: int = 20
    ) -> Page[dict]:
        if actor.role not in {"TEACHER", "ADMIN"}:
            raise DomainError("FORBIDDEN", 403, "Teacher or administrator access required.")
        pagination(page, page_size)
        course = self.repository.course(course_id)
        if course is None:
            raise DomainError("NOT_FOUND", 404, "Course not found.")
        if actor.role != "ADMIN" and course.teacher_id != actor.id:
            raise DomainError("FORBIDDEN", 403, "Course belongs to another teacher.")
        items, total = self.repository.members(course_id, page, page_size)
        return Page(items, page, page_size, total)
