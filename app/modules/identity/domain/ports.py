"""Small persistence and crypto contracts for the identity use cases."""

from datetime import datetime
from types import TracebackType
from typing import Protocol

from .models import Actor, AuthSession, User


class UserRepository(Protocol):
    def get_by_email(self, email: str) -> User | None: ...
    def get_by_id(self, user_id: int) -> User | None: ...


class SessionRepository(Protocol):
    def get_for_update(self, refresh_token_hash: str) -> AuthSession | None:
        """Lock the matching row until the enclosing transaction ends."""
        ...

    def add(self, session: AuthSession) -> None: ...
    def save(self, session: AuthSession) -> None: ...


class UnitOfWork(Protocol):
    users: UserRepository
    sessions: SessionRepository

    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back any uncommitted changes and release resources/locks."""
        ...

    def commit(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


class PasswordVerifier(Protocol):
    def verify(self, password: str, password_hash: str | None) -> bool:
        """Use a dummy hash for a missing user, and never accept a None hash."""
        ...


class TokenIssuer(Protocol):
    def issue(self, actor: Actor, now: datetime) -> str:
        """Issue a signed, time-limited access JWT; never persist plaintext."""
        ...


class Clock(Protocol):
    def __call__(self) -> datetime:
        """Return an aware UTC datetime."""
        ...
