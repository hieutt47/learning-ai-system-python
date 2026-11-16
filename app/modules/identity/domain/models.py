"""Identity records and failures; no HTTP, ORM or crypto-library dependencies."""

from dataclasses import dataclass
from datetime import datetime


class DomainError(Exception):
    def __init__(self, code: str, message: str, *, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.field = field


@dataclass(frozen=True)
class User:
    id: int
    full_name: str
    email: str
    password_hash: str
    role: str
    is_active: bool
    email_verified_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class AuthSession:
    id: int | None
    user_id: int
    refresh_token_hash: str
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Actor:
    id: int
    full_name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "Actor":
        return cls(user.id, user.full_name, user.email, user.role, user.is_active, user.created_at)


@dataclass(frozen=True)
class AuthenticationTokens:
    access_token: str
    refresh_token: str
