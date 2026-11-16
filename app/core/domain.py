"""Framework-independent primitives shared by catalog and course use cases."""

from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, Self


class DomainError(Exception):
    def __init__(self, code: str, status: int, message: str):
        super().__init__(message)
        self.code, self.status = code, status


class ResourceInUse(Exception):
    """A restrictive dependency prevents deletion."""


class Actor(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def role(self) -> str: ...


class UnitOfWork(Protocol):
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    page: int
    page_size: int
    total: int


def pagination(page: int, page_size: int) -> None:
    if type(page) is not int or type(page_size) is not int or page < 1 or not 1 <= page_size <= 100:
        raise DomainError("VALIDATION_ERROR", 400, "Invalid page or page_size (maximum 100).")


def required_text(value: str, field: str, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError("VALIDATION_ERROR", 400, f"{field} must not be blank.")
    value = value.strip()
    if maximum is not None and len(value) > maximum:
        raise DomainError("VALIDATION_ERROR", 400, f"{field} exceeds {maximum} characters.")
    return value
