"""Application factory and liveness endpoint for the initial skeleton."""

from contextlib import asynccontextmanager
from threading import Lock

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import register_error_handlers
from app.core.request_id import RequestIdMiddleware
from app.modules.catalog.api import router as catalog_router
from app.modules.course.api import router as course_router
from app.modules.identity.api.router import router as identity_router
from app.modules.identity.infrastructure.settings import AuthSettings


def create_app(
    settings: Settings | None = None, *, auth_settings: AuthSettings | None = None
) -> FastAPI:
    settings = settings if settings is not None else Settings()

    # Keep debug tracebacks disabled so internal failures retain the error envelope.
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            engine = getattr(app.state, "identity_engine", None)
            if engine is not None:
                engine.dispose()

    application = FastAPI(
        title=settings.name,
        debug=False,
        lifespan=lifespan,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    application.state.identity_engine_lock = Lock()
    application.state.settings = settings
    try:
        auth_settings = auth_settings or AuthSettings()
        if settings.environment == "production" and not auth_settings.cookie_secure:
            auth_settings = None
    except (ValidationError, ValueError):
        auth_settings = None
    application.state.auth_settings = auth_settings
    if auth_settings and auth_settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=auth_settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
        )
    application.add_middleware(RequestIdMiddleware)
    register_error_handlers(application)
    application.include_router(identity_router)
    application.include_router(catalog_router)
    application.include_router(course_router)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
