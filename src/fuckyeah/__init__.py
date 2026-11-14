"""Python client for the FuckYeah machine API.

Create a :class:`FuckYeahClient` with a personal API token. The token is sent
as a bearer token on every request.
"""

from fuckyeah._version import __version__
from fuckyeah.client import DEFAULT_BASE_URL, FuckYeahClient
from fuckyeah.exceptions import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    FuckYeahError,
    NotFoundError,
    RateLimitError,
    TransportError,
    ValidationError,
)
from fuckyeah.models import (
    ApiVersion,
    Author,
    Device,
    MinAppVersions,
    Pattern,
    PatternPage,
    Phase,
    RateLimit,
)

__all__ = [
    "APIError",
    "ApiVersion",
    "AuthenticationError",
    "Author",
    "AuthorizationError",
    "DEFAULT_BASE_URL",
    "Device",
    "FuckYeahClient",
    "FuckYeahError",
    "MinAppVersions",
    "NotFoundError",
    "Pattern",
    "PatternPage",
    "Phase",
    "RateLimit",
    "RateLimitError",
    "TransportError",
    "ValidationError",
    "__version__",
]
