"""Catalog use cases/ports; authenticated active actors are supplied upstream."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
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


class DuplicateName(Exception):
    """Adapters translate category unique violations, including at commit, to this error."""


@dataclass(frozen=True)
class Category:
    id: int | None
    name: str
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CategoryRepository(Protocol):
    """Stable created_at DESC, id DESC pages; save returns DB timestamps/ID.

    Locks last until UoW exit.

    Uniqueness follows database collation. Delete checks and foreign-key restrictions
    must protect concurrent course insertion; translate conflicts to ResourceInUse.
    """

    def list(self, page: int, page_size: int) -> tuple[list[Category], int]: ...
    def get(self, category_id: int, *, for_update: bool = False) -> Category | None: ...
    def name_exists(self, name: str, *, excluding_id: int | None = None) -> bool: ...
    def save(self, category: Category) -> Category: ...
    def has_courses(self, category_id: int) -> bool: ...
    def delete(self, category_id: int) -> None: ...


class CatalogService:
    def __init__(self, repository: CategoryRepository, transaction: Callable[[], UnitOfWork]):
        self.repository, self.transaction = repository, transaction

    def list(self, page: int = 1, page_size: int = 20) -> Page[Category]:
        pagination(page, page_size)
        items, total = self.repository.list(page, page_size)
        return Page(items, page, page_size, total)

    @staticmethod
    def _admin(actor: Actor) -> None:
        if actor.role != "ADMIN":
            raise DomainError("FORBIDDEN", 403, "Administrator access required.")

    def _save(self, category: Category) -> Category:
        if self.repository.name_exists(category.name, excluding_id=category.id):
            raise DuplicateName()
        return self.repository.save(category)

    def create(self, actor: Actor, name: str, description: str | None = None) -> Category:
        self._admin(actor)
        category = Category(None, required_text(name, "name", 120), description)
        try:
            with self.transaction() as uow:
                result = self._save(category)
                uow.commit()
                return result
        except DuplicateName as exc:
            raise DomainError("INVALID_STATE", 409, "Category name already exists.") from exc

    def update(
        self,
        actor: Actor,
        category_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Category:
        self._admin(actor)
        try:
            with self.transaction() as uow:
                category = self.repository.get(category_id, for_update=True)
                if category is None:
                    raise DomainError("NOT_FOUND", 404, "Category not found.")
                result = self._save(
                    replace(
                        category,
                        name=category.name if name is None else required_text(name, "name", 120),
                        description=category.description if description is None else description,
                    )
                )
                uow.commit()
                return result
        except DuplicateName as exc:
            raise DomainError("INVALID_STATE", 409, "Category name already exists.") from exc

    def delete(self, actor: Actor, category_id: int) -> None:
        self._admin(actor)
        try:
            with self.transaction() as uow:
                if self.repository.get(category_id, for_update=True) is None:
                    raise DomainError("NOT_FOUND", 404, "Category not found.")
                if self.repository.has_courses(category_id):
                    raise ResourceInUse()
                self.repository.delete(category_id)
                uow.commit()
        except ResourceInUse as exc:
            raise DomainError("RESOURCE_IN_USE", 409, "Category is used by courses.") from exc
