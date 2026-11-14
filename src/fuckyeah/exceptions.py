"""Errors raised by the FuckYeah client."""

from __future__ import annotations

from typing import Any


class FuckYeahError(Exception):
    """Base class for errors produced by this client."""


class TransportError(FuckYeahError):
    """The request failed before the API returned a response."""


class APIError(FuckYeahError):
    """The API returned a response the client could not treat as success."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.body = body
        if status_code is None:
            super().__init__(message)
        else:
            super().__init__(f"{status_code} {message}")


class AuthenticationError(APIError):
    """The token is missing, invalid, expired, or revoked (HTTP 401)."""


class AuthorizationError(APIError):
    """The token lacks the required scope, or the account email is unverified (HTTP 403)."""


class NotFoundError(APIError):
    """The device or pattern does not exist, or is not visible to this token (HTTP 404)."""


class ValidationError(APIError):
    """The request failed validation (HTTP 422).

    ``errors`` maps each rejected field to the messages returned for it.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
        errors: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body)
        self.errors = errors or {}


class RateLimitError(APIError):
    """Too many requests (HTTP 429).

    ``retry_after`` is the number of seconds from the ``Retry-After`` header,
    when the API sent one. Automatic retries give up once the wait would
    exceed 60 seconds, or once ``max_retries`` is exhausted.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body)
        self.retry_after = retry_after
