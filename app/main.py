"""Application factory and liveness endpoint for the initial skeleton."""

from fastapi import FastAPI

from app.core.config import Settings
from app.core.errors import register_error_handlers
from app.core.request_id import RequestIdMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    # Keep debug tracebacks disabled so internal failures retain the error envelope.
    application = FastAPI(title=settings.name, debug=False)
    application.state.settings = settings
    application.add_middleware(RequestIdMiddleware)
    register_error_handlers(application)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
