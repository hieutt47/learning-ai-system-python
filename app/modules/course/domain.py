"""Teacher/admin course management, not public marketplace or publication workflows.

TASK005 supports draft writes only: published/archived PATCH and DELETE return 409.
This is a scope limitation, not a decision about eventual published-edit policy.
Callers supply an authenticated active actor; identity/session validation stays upstream.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.core.domain import (
    Actor,
    DomainError,
    Page,
    ResourceInUse,
    UnitOfWork,
    pagination,
    required_text,
)


@dataclass(frozen=True)
class Course:
    id: int | None
    title: str
    description: str
    category_id: int
    teacher_id: int
    price: Decimal
    is_published: bool = False
    archived_at: datetime | None = None
    thumbnail_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    category_name: str | None = None
    teacher_name: str | None = None
    lesson_count: int | None = None
    total_students: int | None = None


class CourseCategoryPort(Protocol):
    """Narrow category lookup; lock prevents deletion before the course write commits."""

    def exists(self, category_id: int, *, for_update: bool = False) -> bool: ...


class CourseRepository(Protocol):
    """Stable created_at DESC, id DESC pages, filtered by teacher, with matching total.

    get(for_update=True) locks until UoW exit. save returns persisted ID/timestamps.
    dependencies includes ALL restrictive links (lessons, memberships, AI provenance,
    etc.). delete never cascades; restricted-delete races raise ResourceInUse.
    Repository, category port and UoW must share the same transaction/session.
    """

    def list(
        self, page: int, page_size: int, *, teacher_id: int | None
    ) -> tuple[list[Course], int]: ...
    def get(self, course_id: int, *, for_update: bool = False) -> Course | None: ...
    def save(self, course: Course) -> Course: ...
    def dependencies(self, course_id: int) -> tuple[str, ...]: ...
    def delete(self, course_id: int) -> None: ...


def valid_price(price: Decimal) -> Decimal:
    if (
        not isinstance(price, Decimal)
        or not price.is_finite()
        or price < 0
        or price >= Decimal("1000000000000")
        or price.as_tuple().exponent < -2
    ):
        raise DomainError("VALIDATION_ERROR", 400, "price must fit nonnegative DECIMAL(14,2).")
    return price


class CourseService:
    def __init__(
        self,
        repository: CourseRepository,
        categories: CourseCategoryPort,
        transaction: Callable[[], UnitOfWork],
    ):
        self.repository, self.categories, self.transaction = repository, categories, transaction

    @staticmethod
    def _manager(actor: Actor) -> None:
        if actor.role not in {"TEACHER", "ADMIN"}:
            raise DomainError("FORBIDDEN", 403, "Teacher or administrator access required.")

    def _get(self, actor: Actor, course_id: int, *, for_update: bool = False) -> Course:
        self._manager(actor)
        course = self.repository.get(course_id, for_update=for_update)
        if course is None:
            raise DomainError("NOT_FOUND", 404, "Course not found.")
        if actor.role != "ADMIN" and course.teacher_id != actor.id:
            raise DomainError("FORBIDDEN", 403, "Course belongs to another teacher.")
        return course

    @staticmethod
    def _draft(course: Course) -> None:
        if course.is_published or course.archived_at is not None:
            raise DomainError("INVALID_STATE", 409, "Only draft writes are supported in TASK005.")

    def _category(self, category_id: int) -> None:
        if not self.categories.exists(category_id, for_update=True):
            raise DomainError("NOT_FOUND", 404, "Category not found.")

    def list(self, actor: Actor, page: int = 1, page_size: int = 20) -> Page[Course]:
        self._manager(actor)
        pagination(page, page_size)
        items, total = self.repository.list(
            page,
            page_size,
            teacher_id=None if actor.role == "ADMIN" else actor.id,
        )
        return Page(items, page, page_size, total)

    def get(self, actor: Actor, course_id: int) -> Course:
        return self._get(actor, course_id)

    def create(
        self, actor: Actor, *, title: str, description: str, category_id: int, price: Decimal
    ) -> Course:
        self._manager(actor)
        course = Course(
            None,
            required_text(title, "title", 200),
            required_text(description, "description"),
            category_id,
            actor.id,
            valid_price(price),
        )
        with self.transaction() as uow:
            self._category(category_id)
            result = self.repository.save(course)
            uow.commit()
            return result

    def update(
        self,
        actor: Actor,
        course_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        category_id: int | None = None,
        price: Decimal | None = None,
    ) -> Course:
        self._manager(actor)
        with self.transaction() as uow:
            course = self._get(actor, course_id, for_update=True)
            self._draft(course)
            if category_id is not None:
                self._category(category_id)
            result = self.repository.save(
                replace(
                    course,
                    title=course.title if title is None else required_text(title, "title", 200),
                    description=(
                        course.description
                        if description is None
                        else required_text(description, "description")
                    ),
                    category_id=course.category_id if category_id is None else category_id,
                    price=course.price if price is None else valid_price(price),
                )
            )
            uow.commit()
            return result

    def delete(self, actor: Actor, course_id: int) -> None:
        self._manager(actor)
        try:
            with self.transaction() as uow:
                course = self._get(actor, course_id, for_update=True)
                self._draft(course)
                if self.repository.dependencies(course_id):
                    raise ResourceInUse()
                self.repository.delete(course_id)
                uow.commit()
        except ResourceInUse as exc:
            raise DomainError("INVALID_STATE", 409, "Course has dependent resources.") from exc
