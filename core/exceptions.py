"""HTTP exceptions mapped to status codes by the application kernel."""

from __future__ import annotations

from typing import Any


class HttpException(Exception):
    status_code = 500
    default_message = "Internal Server Error"

    def __init__(self, message: str | None = None, *, status_code: int | None = None):
        self.message = message or self.default_message
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)


class BadRequest(HttpException):
    status_code = 400
    default_message = "Bad Request"


class Unauthorized(HttpException):
    status_code = 401
    default_message = "Unauthorized"


class Forbidden(HttpException):
    status_code = 403
    default_message = "Forbidden"


class NotFound(HttpException):
    status_code = 404
    default_message = "Not Found"


class MethodNotAllowed(HttpException):
    status_code = 405
    default_message = "Method Not Allowed"


class TokenMismatch(HttpException):
    status_code = 419
    default_message = "Page expired. Please refresh and try again."


class Throttled(HttpException):
    status_code = 429
    default_message = "Too many requests."


class ValidationException(HttpException):
    status_code = 422
    default_message = "The given data was invalid."

    def __init__(self, errors: dict[str, list[str]] | None = None, message: str | None = None):
        self.errors: dict[str, list[str]] = errors or {}
        super().__init__(message or self.default_message)


class ApiException(HttpException):
    """Raised inside API routes; rendered as JSON."""

    def __init__(self, message: str, *, status_code: int = 400, errors: Any = None):
        self.errors = errors
        super().__init__(message, status_code=status_code)
