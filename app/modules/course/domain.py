"""Teacher/admin course management, not public marketplace or publication workflows.

TASK005 supports draft writes only: published/archived PATCH and DELETE return 409.
This is a scope limitation, not a decision about eventual published-edit policy.
Callers supply an authenticated active actor; identity/session validation stays upstream.
"""

from __future__ import annotations

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
    thumbnail_storage_key: str | None = None


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
    def save_thumbnail(self, course_id: int, storage_key: str, thumbnail_url: str) -> Course: ...
    def set_published(self, course_id: int, is_published: bool) -> Course: ...
    def archive(self, course_id: int) -> Course: ...
    def search_public(
        self,
        *,
        q: str | None,
        category_id: int | None,
        sort: str,
        page: int,
        page_size: int,
    ) -> tuple[list[dict], int]: ...
    def get_public(self, course_id: int) -> dict | None: ...
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

    def update_thumbnail(
        self, actor: Actor, course_id: int, storage_key: str, thumbnail_url: str
    ) -> tuple[Course, str | None]:
        self._manager(actor)
        with self.transaction() as uow:
            course = self._get(actor, course_id, for_update=True)
            old_key = course.thumbnail_storage_key
            updated = self.repository.save_thumbnail(course_id, storage_key, thumbnail_url)
            uow.commit()
            return updated, old_key

    def set_published(self, actor: Actor, course_id: int, is_published: bool) -> Course:
        self._manager(actor)
        with self.transaction() as uow:
            course = self._get(actor, course_id, for_update=True)
            if is_published:
                if course.archived_at is not None:
                    raise DomainError("INVALID_STATE", 409, "Archived courses cannot be published.")
                if course.is_published:
                    return course
                if not course.title.strip():
                    raise DomainError(
                        "INVALID_STATE", 409, "Course cannot be published: title is required."
                    )
                if not course.description.strip():
                    raise DomainError(
                        "INVALID_STATE",
                        409,
                        "Course cannot be published: description is required.",
                    )
                if not course.thumbnail_storage_key:
                    raise DomainError(
                        "INVALID_STATE",
                        409,
                        "Course cannot be published: thumbnail is required.",
                    )
                if course.price != Decimal("0"):
                    raise DomainError(
                        "COURSE_NOT_FREE",
                        409,
                        "Only free courses can be published in this version.",
                    )
                if (course.lesson_count or 0) < 1:
                    raise DomainError(
                        "INVALID_STATE",
                        409,
                        "Course cannot be published: at least one lesson is required.",
                    )
            elif not course.is_published:
                return course
            updated = self.repository.set_published(course_id, is_published)
            uow.commit()
            return updated

    def archive(self, actor: Actor, course_id: int) -> Course:
        self._manager(actor)
        with self.transaction() as uow:
            course = self._get(actor, course_id, for_update=True)
            if course.archived_at is not None:
                return course
            updated = self.repository.archive(course_id)
            uow.commit()
            return updated


_PUBLIC_SORTS = {
    None: "created_at,desc",
    "": "created_at,desc",
    "created_at,desc": "created_at,desc",
    "created_at,asc": "created_at,asc",
    "title,asc": "title,asc",
    "title,desc": "title,desc",
}


class MarketplaceService:
    def __init__(self, repository: CourseRepository, categories: CourseCategoryPort):
        self.repository, self.categories = repository, categories

    def search(
        self,
        *,
        q: str | None = None,
        category_id: int | None = None,
        sort: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[dict]:
        pagination(page, page_size)
        normalized_q = q.strip() if q is not None else None
        if normalized_q == "":
            normalized_q = None
        if normalized_q is not None and len(normalized_q) > 200:
            raise DomainError("VALIDATION_ERROR", 400, "q must be at most 200 characters.")
        if sort not in _PUBLIC_SORTS:
            raise DomainError("VALIDATION_ERROR", 400, "Unsupported sort parameter.")
        if category_id is not None and not self.categories.exists(category_id):
            raise DomainError("NOT_FOUND", 404, "Category not found.")
        items, total = self.repository.search_public(
            q=normalized_q,
            category_id=category_id,
            sort=_PUBLIC_SORTS[sort],
            page=page,
            page_size=page_size,
        )
        return Page(items, page, page_size, total)

    def get_public(self, course_id: int) -> dict:
        course = self.repository.get_public(course_id)
        if course is None:
            raise DomainError("NOT_FOUND", 404, "Course not found.")
        return course
