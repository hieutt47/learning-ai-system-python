"""The four minimal identity endpoints; registration/email flows are not exposed."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.modules.identity.api.dependencies import (
    current_actor,
    get_auth_settings,
    get_codec,
    get_identity_service,
    invoke,
    optional_bearer_guard,
)
from app.modules.identity.domain.models import Actor
from app.modules.identity.domain.service import IdentityService
from app.modules.identity.infrastructure.crypto import JwtCodec
from app.modules.identity.infrastructure.settings import AuthSettings

router = APIRouter(prefix="/api/v1")
Service = Annotated[IdentityService, Depends(get_identity_service)]
Codec = Annotated[JwtCodec, Depends(get_codec)]
Configuration = Annotated[AuthSettings, Depends(get_auth_settings)]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=72)


class AuthResponse(BaseModel):
    access_token: str


def set_cookie(response: Response, value: str, settings: AuthSettings, *, clear=False):
    response.set_cookie(
        "refreshToken",
        value,
        max_age=0 if clear else 604800,
        path="/api/v1/auth",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post("/auth/login", response_model=AuthResponse)
def login(body: LoginRequest, response: Response, service: Service, settings: Configuration):
    tokens = invoke(service.login, body.email, body.password)
    set_cookie(response, tokens.refresh_token, settings)
    return AuthResponse(access_token=tokens.access_token)


@router.post("/auth/refresh", response_model=AuthResponse)
def refresh(
    request: Request, response: Response, service: Service, codec: Codec, settings: Configuration
):
    optional_bearer_guard(request, service, codec)
    tokens = invoke(service.refresh, request.cookies.get("refreshToken"))
    set_cookie(response, tokens.refresh_token, settings)
    return AuthResponse(access_token=tokens.access_token)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, service: Service, codec: Codec, settings: Configuration):
    optional_bearer_guard(request, service, codec)
    invoke(service.logout, request.cookies.get("refreshToken"))
    response = Response(status_code=204)
    set_cookie(response, "", settings, clear=True)
    return response


@router.get("/users/me")
def me(actor: Annotated[Actor, Depends(current_actor)]) -> dict:
    return {
        "id": str(actor.id),
        "full_name": actor.full_name,
        "email": actor.email,
        "role": actor.role,
        "is_active": actor.is_active,
        "created_at": actor.created_at.isoformat().replace("+00:00", "Z"),
    }
