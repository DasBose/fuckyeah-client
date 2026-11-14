"""Authenticated client for the FuckYeah API v2."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal, Mapping, Sequence
from urllib.parse import quote

import httpx

from fuckyeah._version import __version__
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
    Device,
    Pattern,
    PatternPage,
    Phase,
    RateLimit,
    phases_to_api,
    validate_device_name,
    validate_pattern_name,
)

DEFAULT_BASE_URL = "https://api.fuckyeah.uk/v2"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 2
# Give up rather than block the caller when Retry-After asks for longer than this.
MAX_RETRY_WAIT = 60.0
_NO_BODY = object()
_UNSET = object()

RelayMode = Literal["on", "off", "pattern"]
_RELAY_MODES = ("on", "off", "pattern")
_PATTERN_LOAD_MIN = 1
_PATTERN_LOAD_MAX = 20

_SUCCESS_WITH_DATA = frozenset({200, 201})


def _normalize_token(token: str) -> str:
    if not isinstance(token, str):
        raise TypeError("token must be a string")
    value = token.strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if value == "":
        raise ValueError("token must not be empty")
    if not value.startswith("fy_"):
        raise ValueError(
            "FuckYeah personal API tokens start with 'fy_'. "
            "Create one from your account in the app."
        )
    if (
        not value.isascii()
        or not value.isprintable()
        or any(character.isspace() for character in value)
    ):
        raise ValueError("token contains characters that cannot be sent in an Authorization header")
    return value


def _normalize_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or base_url.strip() == "":
        raise ValueError("base_url must be a non-empty string")
    value = base_url.strip()
    if not value.startswith(("https://", "http://")):
        raise ValueError("base_url must start with https:// or http://")
    return value.rstrip("/") + "/"


def _plain_id(value: object, *, resource: type[Device] | type[Pattern]) -> str:
    if isinstance(value, resource):
        text = value.id
    elif isinstance(value, (Device, Pattern)):
        raise TypeError(f"expected a {resource.__name__} id, got {type(value).__name__}")
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise TypeError(f"{resource.__name__} id must be a string or {resource.__name__}")
    if text == "":
        raise TypeError(f"{resource.__name__} id must not be empty")
    return text


def _path_id(value: object, *, resource: type[Device] | type[Pattern]) -> str:
    return quote(_plain_id(value, resource=resource), safe="")


def _pattern_type_param(types: str | Sequence[str] | None) -> str | None:
    if types is None:
        return None
    if isinstance(types, str):
        items = [part.strip() for part in types.split(",") if part.strip()]
    elif isinstance(types, Sequence):
        items = []
        for item in types:
            if not isinstance(item, str) or item.strip() == "":
                raise TypeError("types must be strings such as 'user', 'public', or 'default'")
            items.append(item.strip())
    else:
        raise TypeError("types must be a string or a sequence of strings")
    if not items:
        raise ValueError("types must include at least one pattern type")
    return ",".join(items)


def _optional_speed(value: float | None, field: str, low: float, high: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
    number = float(value)
    if number < low or number > high:
        raise ValueError(f"{field} must be between {low:g} and {high:g}")
    return number


def _relay(value: RelayMode | None, field: str) -> RelayMode | None:
    if value is None:
        return None
    if value not in _RELAY_MODES:
        raise ValueError(f"{field} must be one of: on, off, pattern")
    return value


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())


def _header_int(response: httpx.Response, name: str) -> int | None:
    raw = response.headers.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _unwrap_data(payload: Any) -> Any:
    if not isinstance(payload, dict) or "data" not in payload:
        raise FuckYeahError("Response is missing 'data'")
    return payload["data"]


def _cursor(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise FuckYeahError(f"Response field {field!r} is not a string")


class FuckYeahClient:
    """Client for the FuckYeah API.

    Authentication happens here. Pass the personal API token created in the
    app (it starts with ``fy_``). Every later request sends
    ``Authorization: Bearer <token>``. The token is kept on this object and
    is not included in :func:`repr`.

    The token is checked for shape only. The API has no token-inspection
    endpoint, and a token can be valid while still lacking a scope, so a
    rejected token surfaces as :class:`~fuckyeah.AuthenticationError` on the
    first protected call.

    Use the client as a context manager so the underlying connection closes::

        with FuckYeahClient(token) as client:
            devices = client.list_devices()

    Redirects are not followed. That keeps the bearer token on the API host
    you configured. HTTP 429 responses are retried up to ``max_retries`` times
    when ``Retry-After`` is at most 60 seconds.

    The default host is API v2, ``https://api.fuckyeah.uk/v2``. Within v2 the
    service only adds fields, so unknown response fields are ignored.
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = _normalize_token(token)
        self.base_url = _normalize_base_url(base_url)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        self.timeout = float(timeout)
        self.max_retries = max_retries
        self.rate_limit: RateLimit | None = None
        self._revoked = False
        self._closed = False
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            follow_redirects=False,
            transport=transport,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "User-Agent": f"fuckyeah/{__version__}",
            },
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self.base_url!r})"

    def __enter__(self) -> FuckYeahClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection. Further calls raise."""
        if self._closed:
            return
        self._closed = True
        self._http.close()

    def get_version(self) -> ApiVersion:
        """Return the API version and the oldest supported native app versions.

        This endpoint does not require a scope. A token is still sent because
        the client is created with one.
        """
        payload = self._request("GET", "", expected=_SUCCESS_WITH_DATA)
        data = _unwrap_data(payload)
        if not isinstance(data, dict):
            raise FuckYeahError("Response field 'data' is not an object")
        return ApiVersion.from_api(data)

    def list_devices(self) -> list[Device]:
        """List devices registered to this account.

        Required scope: ``devices:read``.
        """
        payload = self._request("GET", "devices", expected=_SUCCESS_WITH_DATA)
        return [_device(item) for item in _data_list(payload)]

    def register_device(self, name: str, pin: int) -> Device:
        """Claim a connected, unregistered device with the PIN on its screen.

        The PIN rotates, so register the device soon after reading it.
        Required scope: ``devices:manage``.
        """
        if isinstance(pin, bool) or not isinstance(pin, int):
            raise TypeError("pin must be an integer")
        payload = self._request(
            "POST",
            "devices",
            json_body={"name": validate_device_name(name), "pin": pin},
            expected=frozenset({201}),
        )
        return _device(_unwrap_data(payload))

    def get_device(self, device: str | Device) -> Device:
        """Return one device, including the patterns loaded on it.

        Required scope: ``devices:read``.
        """
        device_id = _path_id(device, resource=Device)
        payload = self._request("GET", f"devices/{device_id}", expected=_SUCCESS_WITH_DATA)
        return _device(_unwrap_data(payload))

    def rename_device(self, device: str | Device, name: str) -> Device:
        """Rename a device. Required scope: ``devices:manage``."""
        device_id = _path_id(device, resource=Device)
        payload = self._request(
            "PUT",
            f"devices/{device_id}",
            json_body={"name": validate_device_name(name)},
            expected=_SUCCESS_WITH_DATA,
        )
        return _device(_unwrap_data(payload))

    def delete_device(self, device: str | Device) -> None:
        """Delete a device so it must be registered again before online use.

        Patterns loaded on it are detached and remain available to other
        devices. Required scope: ``devices:manage``.
        """
        device_id = _path_id(device, resource=Device)
        self._request("DELETE", f"devices/{device_id}", expected=frozenset({204}))

    def load_patterns(
        self,
        device: str | Device,
        pattern_ids: Sequence[str | Pattern],
    ) -> Device:
        """Replace the patterns loaded on a device.

        IDs are loaded in the order given. Each pattern must be one you own
        or a default pattern. One to twenty unique patterns are allowed. The
        device applies the list once it is connected and idle.

        Required scope: ``devices:control``.
        """
        device_id = _path_id(device, resource=Device)
        if isinstance(pattern_ids, (str, bytes)) or not isinstance(pattern_ids, Sequence):
            raise TypeError("pattern_ids must be a sequence of pattern IDs")
        ids = [_plain_id(pattern, resource=Pattern) for pattern in pattern_ids]
        if len(ids) < _PATTERN_LOAD_MIN or len(ids) > _PATTERN_LOAD_MAX:
            raise ValueError(
                f"pattern_ids must contain between {_PATTERN_LOAD_MIN} "
                f"and {_PATTERN_LOAD_MAX} items"
            )
        if len(set(ids)) != len(ids):
            raise ValueError("pattern_ids must be unique")
        payload = self._request(
            "PUT",
            f"devices/{device_id}/patterns",
            json_body={"pattern_ids": ids},
            expected=_SUCCESS_WITH_DATA,
        )
        return _device(_unwrap_data(payload))

    def run_pattern(
        self,
        device: str | Device,
        *,
        pattern_id: str | Pattern | None = None,
        speed: float | None = None,
        stroke_min: float | None = None,
        stroke_length: float | None = None,
        r1: RelayMode | None = None,
        r2: RelayMode | None = None,
    ) -> None:
        """Run a loaded pattern, or adjust the pattern that is already running.

        Every argument is optional. Omit ``pattern_id`` to change the speed,
        stroke, or relays of the current pattern. ``speed``, ``stroke_min``,
        and ``stroke_length`` are fractions of the room left by the device's
        own settings and the active pattern phase. ``r1`` and ``r2`` are
        ``"on"``, ``"off"``, or ``"pattern"``.

        The API accepts the command with HTTP 202 and does not confirm that
        the device applied it. The device must be online, with remote control
        enabled and a usable local speed and stroke range.

        Required scope: ``devices:control``.
        """
        device_id = _path_id(device, resource=Device)
        body: dict[str, Any] = {}
        if pattern_id is not None:
            body["pattern_id"] = _plain_id(pattern_id, resource=Pattern)
        speed_value = _optional_speed(speed, "speed", 0, 1)
        stroke_min_value = _optional_speed(stroke_min, "stroke_min", 0, 0.99)
        stroke_length_value = _optional_speed(stroke_length, "stroke_length", 0.01, 1)
        if speed_value is not None:
            body["speed"] = speed_value
        if stroke_min_value is not None:
            body["stroke_min"] = stroke_min_value
        if stroke_length_value is not None:
            body["stroke_length"] = stroke_length_value
        r1_value = _relay(r1, "r1")
        r2_value = _relay(r2, "r2")
        if r1_value is not None:
            body["r1"] = r1_value
        if r2_value is not None:
            body["r2"] = r2_value
        self._request(
            "POST",
            f"devices/{device_id}/command/pattern",
            json_body=body,
            expected=frozenset({202}),
        )

    def stop_device(self, device: str | Device) -> None:
        """Stop a device: speed 0, both relays off.

        Those values stick until the next :meth:`run_pattern` call, which
        should send ``speed`` again (and ``r1`` or ``r2`` when the relays
        should leave the off state). As with other commands, HTTP 202 means
        the API accepted the request, not that the device confirmed the stop.

        Required scope: ``devices:control``.
        """
        device_id = _path_id(device, resource=Device)
        self._request(
            "POST",
            f"devices/{device_id}/command/stop",
            expected=frozenset({202}),
        )

    def list_patterns(
        self,
        *,
        types: str | Sequence[str] | None = None,
        query: str | None = None,
        cursor: str | None = None,
    ) -> PatternPage:
        """List one page of patterns.

        ``types`` filters to ``user`` (yours), ``public``, ``default``, or a
        comma-separated combination. Omit it to include all three. ``query``
        keeps patterns whose name contains that text. Results are ordered by
        name, 20 per page. Pass ``cursor`` from the previous page's
        ``next_cursor`` (or ``prev_cursor``) to move.

        Required scope: ``patterns:read``.
        """
        if query is not None and not isinstance(query, str):
            raise TypeError("query must be a string")
        if cursor is not None and not isinstance(cursor, str):
            raise TypeError("cursor must be a string")
        params = {
            "type": _pattern_type_param(types),
            "q": query,
            "cursor": cursor,
        }
        payload = self._request(
            "GET",
            "patterns",
            params=params,
            expected=_SUCCESS_WITH_DATA,
        )
        return _pattern_page(payload)

    def iter_patterns(
        self,
        *,
        types: str | Sequence[str] | None = None,
        query: str | None = None,
    ) -> Iterator[Pattern]:
        """Yield patterns matching ``types`` and ``query``, following cursors.

        Further pages are requested only as the caller continues. Required
        scope: ``patterns:read``.
        """
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            page = self.list_patterns(types=types, query=query, cursor=cursor)
            yield from page.patterns
            if page.next_cursor is None:
                return
            if page.next_cursor in seen:
                raise FuckYeahError("Pattern pagination returned a cursor that was already used")
            seen.add(page.next_cursor)
            cursor = page.next_cursor

    def create_pattern(
        self,
        name: str,
        phases: Sequence[Phase | Mapping[str, Any]],
        *,
        description: str | None = None,
        public: bool | None = None,
        parent_id: str | Pattern | None = None,
    ) -> Pattern:
        """Create a pattern.

        ``name`` is ASCII and at most 19 characters so it fits on the device
        screen. ``phases`` needs 2 to 150 steps. ``parent_id`` records the
        pattern this one was copied from; the API does not return that id.
        ``public`` shares the pattern with other users when true.

        Required scope: ``patterns:write``.
        """
        body: dict[str, Any] = {
            "name": validate_pattern_name(name),
            "phases": phases_to_api(phases),
        }
        if description is not None:
            if not isinstance(description, str):
                raise TypeError("description must be a string")
            body["description"] = description
        if public is not None:
            if not isinstance(public, bool):
                raise TypeError("public must be a boolean")
            body["public"] = public
        if parent_id is not None:
            body["parent_id"] = _plain_id(parent_id, resource=Pattern)
        payload = self._request("POST", "patterns", json_body=body, expected=frozenset({201}))
        return _pattern(_unwrap_data(payload))

    def get_pattern(self, pattern: str | Pattern) -> Pattern:
        """Return one pattern. Required scope: ``patterns:read``."""
        pattern_id = _path_id(pattern, resource=Pattern)
        payload = self._request("GET", f"patterns/{pattern_id}", expected=_SUCCESS_WITH_DATA)
        return _pattern(_unwrap_data(payload))

    def update_pattern(
        self,
        pattern: str | Pattern,
        *,
        name: str | None = None,
        description: str | None = _UNSET,
        public: bool | None = None,
        phases: Sequence[Phase | Mapping[str, Any]] | None = None,
    ) -> Pattern:
        """Update a pattern you own.

        Only the arguments you pass are sent. Pass ``description=None`` to
        clear the description. Changing anything other than ``public`` creates
        a new revision so devices can tell that they should reload the pattern.

        Required scope: ``patterns:write``.
        """
        pattern_id = _path_id(pattern, resource=Pattern)
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = validate_pattern_name(name)
        if description is not _UNSET:
            if description is not None and not isinstance(description, str):
                raise TypeError("description must be a string or None")
            body["description"] = description
        if public is not None:
            if not isinstance(public, bool):
                raise TypeError("public must be a boolean")
            body["public"] = public
        if phases is not None:
            body["phases"] = phases_to_api(phases)
        if not body:
            raise ValueError("update_pattern requires at least one field to change")
        payload = self._request(
            "PUT",
            f"patterns/{pattern_id}",
            json_body=body,
            expected=_SUCCESS_WITH_DATA,
        )
        return _pattern(_unwrap_data(payload))

    def delete_pattern(self, pattern: str | Pattern) -> None:
        """Delete a pattern you own.

        Copies other people made are not deleted. Required scope: ``patterns:write``.
        """
        pattern_id = _path_id(pattern, resource=Pattern)
        self._request("DELETE", f"patterns/{pattern_id}", expected=frozenset({204}))

    def revoke_token(self) -> None:
        """Revoke the token this client was created with.

        Later calls on this object fail locally. The token can also be revoked
        from the app. This request needs no extra scope beyond a valid token.
        """
        self._request("DELETE", "auth/token", expected=frozenset({204}))
        self._revoked = True

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = _NO_BODY,
        params: Mapping[str, Any] | None = None,
        expected: frozenset[int],
    ) -> Any:
        self._ensure_open()
        retries_left = self.max_retries
        request_kwargs: dict[str, Any] = {}
        if json_body is not _NO_BODY:
            request_kwargs["json"] = json_body
        if params:
            cleaned = {key: value for key, value in params.items() if value is not None}
            if cleaned:
                request_kwargs["params"] = cleaned
        while True:
            try:
                response = self._http.request(method, path, **request_kwargs)
            except httpx.TimeoutException as exc:
                raise TransportError("Request to the FuckYeah API timed out") from exc
            except httpx.RequestError as exc:
                raise TransportError("Request to the FuckYeah API failed") from exc
            self.rate_limit = RateLimit(
                limit=_header_int(response, "x-ratelimit-limit"),
                remaining=_header_int(response, "x-ratelimit-remaining"),
            )
            if response.status_code == 429 and retries_left > 0:
                wait = _retry_after_seconds(response)
                if wait is not None and wait <= MAX_RETRY_WAIT:
                    retries_left -= 1
                    time.sleep(wait)
                    continue
            if response.status_code not in expected:
                raise _error_from_response(response)
            if response.status_code in (202, 204) or not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise FuckYeahError("FuckYeah API returned a response that is not JSON") from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise FuckYeahError("This client is closed")
        if self._revoked:
            raise AuthenticationError("This token was revoked by revoke_token()")


def _device(data: Any) -> Device:
    if not isinstance(data, Mapping):
        raise FuckYeahError("Response field 'data' is not an object")
    return Device.from_api(data)


def _pattern(data: Any) -> Pattern:
    if not isinstance(data, Mapping):
        raise FuckYeahError("Response field 'data' is not an object")
    return Pattern.from_api(data)


def _data_list(payload: Any) -> list[Any]:
    data = _unwrap_data(payload)
    if not isinstance(data, list):
        raise FuckYeahError("Response field 'data' is not an array")
    return data


def _pattern_page(payload: Any) -> PatternPage:
    if not isinstance(payload, dict):
        raise FuckYeahError("Pattern list response is not an object")
    items = _data_list(payload)
    meta = payload.get("meta") or {}
    links = payload.get("links") or {}
    if not isinstance(meta, dict) or not isinstance(links, dict):
        raise FuckYeahError("Pattern list response has invalid pagination")
    per_page = meta.get("per_page", len(items))
    if isinstance(per_page, bool) or not isinstance(per_page, int):
        raise FuckYeahError("Response field 'per_page' is not an integer")
    return PatternPage(
        patterns=tuple(_pattern(item) for item in items),
        per_page=per_page,
        next_cursor=_cursor(meta.get("next_cursor"), "next_cursor"),
        prev_cursor=_cursor(meta.get("prev_cursor"), "prev_cursor"),
        next_url=_cursor(links.get("next"), "next"),
        prev_url=_cursor(links.get("prev"), "prev"),
    )


def _error_from_response(response: httpx.Response) -> APIError:
    message = response.reason_phrase or "Request failed"
    body: Any = None
    errors: dict[str, list[str]] | None = None
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        if text:
            message = text
    else:
        if isinstance(body, dict):
            raw_message = body.get("message")
            if isinstance(raw_message, str) and raw_message:
                message = raw_message
            raw_errors = body.get("errors")
            if isinstance(raw_errors, dict):
                errors = {}
                for field, messages in raw_errors.items():
                    if isinstance(messages, list):
                        errors[str(field)] = [str(item) for item in messages]
                    else:
                        errors[str(field)] = [str(messages)]
        elif body is not None:
            message = str(body)
    status = response.status_code
    if status == 401:
        return AuthenticationError(message, status_code=status, body=body)
    if status == 403:
        return AuthorizationError(message, status_code=status, body=body)
    if status == 404:
        return NotFoundError(message, status_code=status, body=body)
    if status == 422:
        return ValidationError(message, status_code=status, body=body, errors=errors)
    if status == 429:
        return RateLimitError(
            message,
            status_code=status,
            body=body,
            retry_after=_retry_after_seconds(response),
        )
    return APIError(message, status_code=status, body=body)
