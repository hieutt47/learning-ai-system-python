"""Shared HTTP error envelopes without exposing input values or internal failures."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.core.api_errors import ApiError

logger = logging.getLogger(__name__)

HTTP_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "UNPROCESSABLE_ENTITY",
    429: "TOO_MANY_REQUESTS",
}


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = request.state.request_id
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "details": details or [],
            "request_id": request_id,
        },
        headers={**(headers or {}), "X-Request-ID": request_id},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_application_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status, exc.code, str(exc), exc.details, exc.headers)

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(
            request,
            exc.status_code,
            HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR"),
            str(exc.detail),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Do not echo raw input or Pydantic context (which can contain secrets/errors).
        details = [
            {"field": ".".join(map(str, error["loc"][1:])), "message": "Invalid value"}
            for error in exc.errors()
        ]
        return error_response(
            request, 400, "VALIDATION_ERROR", "Request validation failed.", details
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled error request_id=%s", request.state.request_id, exc_info=exc)
        return error_response(
            request, 500, "INTERNAL_SERVER_ERROR", "An unexpected error occurred."
        )
