"""Mappings for existing Flyway tables. Never creates or alters schema."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, select
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.types import TypeDecorator

from app.modules.identity.domain.models import AuthSession, User


class UtcDateTime(TypeDecorator):
    impl = DATETIME(fsp=6)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Expected timezone-aware datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean)
    email_verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class AuthSessionRow(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    refresh_token_hash: Mapped[str] = mapped_column(String(255), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class SqlUsers:
    def __init__(self, session: Session):
        self.session = session

    def get_by_email(self, email: str) -> User | None:
        return self._domain(self.session.scalar(select(UserRow).where(UserRow.email == email)))

    def get_by_id(self, user_id: int) -> User | None:
        return self._domain(self.session.get(UserRow, user_id))

    @staticmethod
    def _domain(row: UserRow | None) -> User | None:
        return (
            None
            if row is None
            else User(
                row.id,
                row.full_name,
                row.email,
                row.password_hash,
                row.role,
                row.is_active,
                row.email_verified_at,
                row.created_at,
            )
        )


class SqlSessions:
    def __init__(self, session: Session):
        self.session = session

    def get_for_update(self, token_hash: str) -> AuthSession | None:
        row = self.session.scalar(
            select(AuthSessionRow)
            .where(AuthSessionRow.refresh_token_hash == token_hash)
            .with_for_update()
        )
        return (
            None
            if row is None
            else AuthSession(
                row.id,
                row.user_id,
                row.refresh_token_hash,
                row.expires_at,
                row.revoked_at,
                row.created_at,
                row.updated_at,
            )
        )

    def add(self, value: AuthSession) -> None:
        self.session.add(
            AuthSessionRow(
                user_id=value.user_id,
                refresh_token_hash=value.refresh_token_hash,
                expires_at=value.expires_at,
                revoked_at=value.revoked_at,
                created_at=value.created_at,
                updated_at=value.updated_at,
            )
        )

    def save(self, value: AuthSession) -> None:
        row = self.session.get(AuthSessionRow, value.id)
        if row is None:
            raise RuntimeError("Locked session disappeared")
        row.refresh_token_hash = value.refresh_token_hash
        row.expires_at = value.expires_at
        row.revoked_at = value.revoked_at
        row.updated_at = value.updated_at


class SqlUnitOfWork:
    def __init__(self, engine: Engine):
        self.engine = engine

    def __enter__(self):
        self.session = Session(self.engine, expire_on_commit=False)
        self.users = SqlUsers(self.session)
        self.sessions = SqlSessions(self.session)
        return self

    def commit(self):
        self.session.commit()

    def __exit__(self, *args):
        try:
            self.session.rollback()
        finally:
            self.session.close()
