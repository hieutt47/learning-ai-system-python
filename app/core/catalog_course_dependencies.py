"""Request-scoped persistence and HTTP boundary for catalog/course adapters."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from pydantic import AfterValidator, Field, ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.database import DatabaseSettings, database_engine
from app.core.domain import DomainError, ResourceInUse


def storage_error() -> ApiError:
    return ApiError(503, "SERVICE_UNAVAILABLE", "Catalog/course storage is unavailable")


def get_session(request: Request) -> Iterator[Session]:
    # Shared with identity and disposed by the application lifespan. No auth secret
    # is needed to compose the public category endpoint.
    with request.app.state.identity_engine_lock:
        if getattr(request.app.state, "identity_engine", None) is None:
            try:
                request.app.state.identity_engine = database_engine(DatabaseSettings())
            except (ValidationError, ValueError):
                raise ApiError(503, "CONFIGURATION_ERROR", "Database is not configured") from None
    session = Session(request.app.state.identity_engine)
    try:
        yield session
    except SQLAlchemyError:
        raise storage_error() from None
    finally:
        try:
            session.rollback()
        finally:
            session.close()


SessionDependency = Annotated[Session, Depends(get_session)]


class SqlUnitOfWork:
    """Use the existing request session; commit/rollback never closes it."""

    def __init__(self, session: Session, *, duplicate_error: type[Exception] | None = None):
        self.session, self.duplicate_error = session, duplicate_error
        self.committed = False

    def __enter__(self):
        return self

    def commit(self) -> None:
        self.session.commit()
        self.committed = True

    def __exit__(self, exc_type, exc, traceback):
        if exc is not None or not self.committed:
            self.session.rollback()
        if isinstance(exc, IntegrityError):
            args = getattr(exc.orig, "args", ())
            code = args[0] if args else None
            if code == 1062 and self.duplicate_error is not None:
                raise self.duplicate_error() from exc
            if code == 1451:
                raise ResourceInUse() from exc
        return False


def invoke(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except DomainError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None
    except SQLAlchemyError:
        raise storage_error() from None


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def positive_bigint(value: str) -> str:
    if int(value) > 9223372036854775807:
        raise ValueError("ID exceeds signed BIGINT")
    return value


WireId = Annotated[
    str, Field(strict=True, pattern=r"^[1-9][0-9]{0,18}$"), AfterValidator(positive_bigint)
]
