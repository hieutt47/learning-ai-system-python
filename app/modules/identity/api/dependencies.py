"""Lazy infrastructure composition and reusable current-actor dependency."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.api_errors import ApiError
from app.core.database import DatabaseSettings, database_engine
from app.modules.identity.domain.models import Actor, DomainError
from app.modules.identity.domain.service import IdentityService
from app.modules.identity.infrastructure.crypto import BCryptVerifier, JwtCodec
from app.modules.identity.infrastructure.persistence import SqlUnitOfWork
from app.modules.identity.infrastructure.settings import AuthSettings


def configuration_error() -> ApiError:
    return ApiError(503, "CONFIGURATION_ERROR", "Authentication is not configured")


def get_auth_settings(request: Request) -> AuthSettings:
    if not getattr(request.app.state, "auth_settings", None):
        raise configuration_error()
    return request.app.state.auth_settings


def get_codec(settings: Annotated[AuthSettings, Depends(get_auth_settings)]) -> JwtCodec:
    return JwtCodec(settings.jwt_secret.get_secret_value())


def get_identity_service(
    request: Request, codec: Annotated[JwtCodec, Depends(get_codec)]
) -> IdentityService:
    with request.app.state.identity_engine_lock:
        if getattr(request.app.state, "identity_engine", None) is None:
            try:
                request.app.state.identity_engine = database_engine(DatabaseSettings())
            except (ValidationError, ValueError):
                raise configuration_error() from None
    engine = request.app.state.identity_engine
    return IdentityService(
        lambda: SqlUnitOfWork(engine), BCryptVerifier(), codec, lambda: datetime.now(UTC)
    )


def translated(error: DomainError) -> ApiError:
    status = {
        "VALIDATION_ERROR": 400,
        "UNAUTHENTICATED": 401,
        "ACCOUNT_INACTIVE": 403,
        "EMAIL_NOT_VERIFIED": 403,
    }.get(error.code, 500)
    details = [{"field": error.field, "message": str(error)}] if error.field else []
    return ApiError(
        status,
        error.code,
        str(error),
        details=details,
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
    )


def invoke(operation, *args):
    try:
        return operation(*args)
    except DomainError as exc:
        raise translated(exc) from None
    except SQLAlchemyError:
        # No driver text, connection URL, SQL parameters or credentials enter the response.
        raise ApiError(
            503, "SERVICE_UNAVAILABLE", "Authentication storage is unavailable"
        ) from None


def bearer(request: Request) -> str | None:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value if scheme.lower() == "bearer" and value else None


def current_actor(
    request: Request,
    service: Annotated[IdentityService, Depends(get_identity_service)],
    codec: Annotated[JwtCodec, Depends(get_codec)],
) -> Actor:
    token = bearer(request)
    if token is None:
        raise translated(DomainError("UNAUTHENTICATED", "Authentication required"))
    return invoke(service.me, invoke(codec.subject, token))


def optional_bearer_guard(request: Request, service: IdentityService, codec: JwtCodec) -> None:
    token = bearer(request)
    if token is None:
        return
    try:
        subject = codec.subject(token)
    except DomainError:
        return  # An invalid/expired bearer does not block cookie-only endpoints.
    try:
        service.me(subject)
    except DomainError as exc:
        if exc.code == "ACCOUNT_INACTIVE":
            raise translated(exc) from None
    except SQLAlchemyError:
        raise ApiError(
            503, "SERVICE_UNAVAILABLE", "Authentication storage is unavailable"
        ) from None
