"""Explicit application failures translated by the shared HTTP boundary."""


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details or []
        self.headers = headers
