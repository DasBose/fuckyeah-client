"""Behaviour tests for FuckYeahClient. HTTP is mocked; no token is sent anywhere."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from fuckyeah import (
    AuthenticationError,
    AuthorizationError,
    FuckYeahClient,
    FuckYeahError,
    NotFoundError,
    Phase,
    RateLimitError,
    TransportError,
    ValidationError,
    __version__,
)

TOKEN = "fy_test_token_value"
DEVICE_ID = "11111111-1111-1111-1111-111111111111"
PATTERN_ID = "22222222-2222-2222-2222-222222222222"
OTHER_PATTERN_ID = "33333333-3333-3333-3333-333333333333"


def pattern_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": PATTERN_ID,
        "created_at": "2026-01-02T03:04:05.000000Z",
        "updated_at": "2026-01-02T03:04:05.000000Z",
        "public": False,
        "name": "Pulse",
        "description": "A short pulse",
        "type": "user",
        "phases": [
            {"p": 0.2, "s": 0.4, "a": 0.5, "d": 0, "r1": False, "r2": False},
            {"p": 0.8, "s": 0.7, "a": 0.5, "d": 0.2, "r1": True, "r2": False},
        ],
        "author": {"id": "author-1", "public_name": "Ada"},
        "future_field": "ignored",
    }
    body.update(overrides)
    return body


def device_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": DEVICE_ID,
        "created_at": "2026-01-02T03:04:05Z",
        "updated_at": None,
        "thing_name": "thing",
        "serial_number": "SN1",
        "last_connection_at": "2026-02-03T04:05:06+00:00",
        "name": "Desk",
        "beta": False,
        "last_sync_at": None,
        "hardware_model": "mark2",
        "hardware_version": 3,
        "firmware_version": "1.2.3",
        "firmware_target_version": None,
        "patterns": [pattern_body()],
        "future_field": "ignored",
    }
    body.update(overrides)
    return body


def client_for(handler, **kwargs) -> FuckYeahClient:
    return FuckYeahClient(TOKEN, transport=httpx.MockTransport(handler), **kwargs)


def json_of(request: httpx.Request) -> object:
    return json.loads(request.content.decode())


def test_package_version_matches_project_metadata() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in text


def test_token_is_required_and_checked_on_creation() -> None:
    with pytest.raises(TypeError):
        FuckYeahClient(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        FuckYeahClient("   ")
    with pytest.raises(ValueError):
        FuckYeahClient("not-a-fuckyeah-token")
    with pytest.raises(ValueError):
        FuckYeahClient("fy_line\nbreak")

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={"data": []})

    client = FuckYeahClient("  Bearer fy_from_header  ", transport=httpx.MockTransport(handler))
    client.list_devices()
    assert seen["authorization"] == "Bearer fy_from_header"
    assert "fy_from_header" not in repr(client)
    client.close()


def test_requests_use_bearer_auth_and_the_v2_base_url() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["accept"] = request.headers["accept"]
        seen["user_agent"] = request.headers["user-agent"]
        return httpx.Response(
            200,
            json={"data": []},
            headers={"x-ratelimit-limit": "1500", "x-ratelimit-remaining": "1499"},
        )

    with client_for(handler) as client:
        assert client.list_devices() == []
        assert client.rate_limit is not None
        assert client.rate_limit.limit == 1500
        assert client.rate_limit.remaining == 1499

    assert seen["url"] == "https://api.fuckyeah.uk/v2/devices"
    assert seen["authorization"] == f"Bearer {TOKEN}"
    assert seen["accept"] == "application/json"
    assert seen["user_agent"] == f"fuckyeah/{__version__}"


def test_closed_client_does_not_send() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    with client_for(handler) as client:
        pass
    with pytest.raises(FuckYeahError, match="closed"):
        client.list_devices()


def test_get_version_accepts_the_live_object_and_the_documented_string() -> None:
    payloads = iter(
        [
            {
                "data": {
                    "version": "2.0.0",
                    "min_app_versions": {"ios": None, "android": "4.1", "watch": "later"},
                }
            },
            {"data": {"version": "2.0.0", "min_app_versions": "1.0"}},
        ]
    )
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=next(payloads))

    with client_for(handler) as client:
        live = client.get_version()
        documented = client.get_version()

    assert urls == ["https://api.fuckyeah.uk/v2/", "https://api.fuckyeah.uk/v2/"]
    assert live.version == "2.0.0"
    assert live.min_app_versions.ios is None
    assert live.min_app_versions.android == "4.1"
    assert live.min_app_versions.raw is None
    assert documented.min_app_versions.raw == "1.0"
    assert documented.min_app_versions.ios is None


def test_device_reads_and_writes() -> None:
    requests: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json_of(request) if request.content else None
        requests.append((request.method, str(request.url), body))
        if request.method == "GET" and request.url.path == "/v2/devices":
            return httpx.Response(200, json={"data": [device_body()]})
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "POST":
            registered = {key: value for key, value in device_body().items() if key != "patterns"}
            return httpx.Response(201, json={"data": registered})
        return httpx.Response(200, json={"data": device_body()})

    with client_for(handler) as client:
        devices = client.list_devices()
        registered = client.register_device("Desk", 1234)
        loaded = client.get_device(registered)
        renamed = client.rename_device(loaded, "Window")
        client.delete_device(renamed)

    assert devices[0].id == DEVICE_ID
    assert devices[0].name == "Desk"
    assert devices[0].hardware_version == 3
    assert devices[0].last_connection_at is not None
    assert devices[0].last_connection_at.tzinfo is not None
    assert devices[0].patterns is not None
    assert devices[0].patterns[0].author is not None
    assert devices[0].patterns[0].author.public_name == "Ada"
    assert registered.patterns is None
    assert [item[0] for item in requests] == ["GET", "POST", "GET", "PUT", "DELETE"]
    assert requests[1][2] == {"name": "Desk", "pin": 1234}
    assert requests[2][1] == f"https://api.fuckyeah.uk/v2/devices/{DEVICE_ID}"
    assert requests[3][2] == {"name": "Window"}
    assert requests[4][1] == f"https://api.fuckyeah.uk/v2/devices/{DEVICE_ID}"


def test_device_commands() -> None:
    requests: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json_of(request) if request.content else None
        requests.append((request.method, str(request.url), body))
        if request.url.path.endswith("/patterns"):
            return httpx.Response(200, json={"data": device_body()})
        return httpx.Response(202)

    with client_for(handler) as client:
        device = client.load_patterns(DEVICE_ID, [PATTERN_ID, OTHER_PATTERN_ID])
        client.run_pattern(
            device,
            pattern_id=PATTERN_ID,
            speed=0.5,
            stroke_min=0.1,
            stroke_length=0.8,
            r1="pattern",
            r2="off",
        )
        client.run_pattern(DEVICE_ID, speed=1)
        client.stop_device(device)

    assert device.serial_number == "SN1"
    assert requests[0][2] == {"pattern_ids": [PATTERN_ID, OTHER_PATTERN_ID]}
    assert requests[1][1].endswith("/command/pattern")
    assert requests[1][2] == {
        "pattern_id": PATTERN_ID,
        "speed": 0.5,
        "stroke_min": 0.1,
        "stroke_length": 0.8,
        "r1": "pattern",
        "r2": "off",
    }
    assert requests[2][2] == {"speed": 1.0}
    assert requests[3] == (
        "POST",
        f"https://api.fuckyeah.uk/v2/devices/{DEVICE_ID}/command/stop",
        None,
    )


def test_pattern_pages_and_writes() -> None:
    pages = iter(
        [
            {
                "data": [pattern_body()],
                "links": {"next": "https://api.example/next", "prev": None},
                "meta": {"per_page": 20, "next_cursor": "cursor-2", "prev_cursor": None},
            },
            {
                "data": [pattern_body(id=OTHER_PATTERN_ID, name="Wave", type="public", author=None)],
                "links": {"next": None, "prev": "https://api.example/prev"},
                "meta": {"per_page": 20, "next_cursor": None, "prev_cursor": "cursor-1"},
            },
        ]
    )
    queries: list[dict[str, str]] = []
    writes: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/patterns"):
            queries.append(dict(request.url.params))
            return httpx.Response(200, json=next(pages))
        body = json_of(request) if request.content else None
        writes.append((request.method, str(request.url), body))
        if request.method == "DELETE":
            return httpx.Response(204)
        status = 201 if request.method == "POST" else 200
        return httpx.Response(status, json={"data": pattern_body(public=True, description=None)})

    phases = [
        {"p": 0, "s": 0.5, "a": 0.5, "d": 0, "r1": False, "r2": False},
        Phase(p=1, s=0.5, a=0.5, d=1, r1=True, r2=False),
    ]
    with client_for(handler) as client:
        found = list(client.iter_patterns(types=["user", "public"], query="pulse"))
        created = client.create_pattern(
            "Pulse",
            phases,
            description="A short pulse",
            public=False,
            parent_id=PATTERN_ID,
        )
        updated = client.update_pattern(created, description=None, public=True)
        client.delete_pattern(updated)

    assert [pattern.name for pattern in found] == ["Pulse", "Wave"]
    assert found[1].author is None
    assert found[1].type == "public"
    assert queries[0] == {"type": "user,public", "q": "pulse"}
    assert queries[1]["cursor"] == "cursor-2"
    assert writes[0][0] == "POST"
    assert writes[0][2]["name"] == "Pulse"
    assert writes[0][2]["public"] is False
    assert writes[0][2]["parent_id"] == PATTERN_ID
    assert writes[0][2]["phases"][1]["r1"] is True
    assert writes[1] == (
        "PUT",
        f"https://api.fuckyeah.uk/v2/patterns/{PATTERN_ID}",
        {"description": None, "public": True},
    )
    assert created.description is None
    assert updated.public is True


def test_repeated_pagination_cursor_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [pattern_body()],
                "links": {},
                "meta": {"per_page": 20, "next_cursor": "again", "prev_cursor": None},
            },
        )

    with client_for(handler) as client:
        with pytest.raises(FuckYeahError, match="cursor"):
            list(client.iter_patterns())


def test_local_validation_happens_before_a_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    with client_for(handler) as client:
        with pytest.raises(TypeError):
            client.register_device("Desk", True)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            client.load_patterns(DEVICE_ID, [PATTERN_ID, PATTERN_ID])
        with pytest.raises(ValueError):
            client.run_pattern(DEVICE_ID, r1="maybe")  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            client.create_pattern("this name is too long", [])
        with pytest.raises(ValueError):
            client.update_pattern(PATTERN_ID)
        with pytest.raises(TypeError):
            Phase(p=0, s=0.5, a=0.5, d=0, r1="no", r2=False)  # type: ignore[arg-type]


def test_api_errors_keep_status_message_and_field_errors() -> None:
    responses = iter(
        [
            httpx.Response(401, json={"message": "Unauthenticated."}),
            httpx.Response(403, json={"message": "Invalid scope."}),
            httpx.Response(404, json={"message": "Not found."}),
            httpx.Response(
                422,
                json={
                    "message": "The name field is required.",
                    "errors": {"name": ["The name field is required."], "pin": "bad"},
                },
            ),
            httpx.Response(500, text="nope"),
            httpx.Response(302, headers={"location": "https://example.invalid"}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    with client_for(handler, max_retries=0) as client:
        with pytest.raises(AuthenticationError, match="Unauthenticated") as auth:
            client.list_devices()
        with pytest.raises(AuthorizationError):
            client.list_devices()
        with pytest.raises(NotFoundError):
            client.get_device(DEVICE_ID)
        with pytest.raises(ValidationError) as invalid:
            client.register_device("Desk", 1)
        with pytest.raises(FuckYeahError, match="500"):
            client.list_devices()
        with pytest.raises(FuckYeahError, match="302"):
            client.list_devices()

    assert auth.value.status_code == 401
    assert invalid.value.errors["name"] == ["The name field is required."]
    assert invalid.value.errors["pin"] == ["bad"]


def test_rate_limit_retries_short_waits_and_surfaces_long_ones() -> None:
    short_calls = {"n": 0}

    def short(request: httpx.Request) -> httpx.Response:
        short_calls["n"] += 1
        if short_calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"message": "slow"})
        return httpx.Response(200, json={"data": []})

    with client_for(short, max_retries=2) as client:
        assert client.list_devices() == []
    assert short_calls["n"] == 3

    long_calls = {"n": 0}

    def long(request: httpx.Request) -> httpx.Response:
        long_calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "120"}, json={"message": "slow"})

    with client_for(long, max_retries=5) as client:
        with pytest.raises(RateLimitError) as limited:
            client.list_devices()
    assert limited.value.retry_after == 120.0
    assert limited.value.status_code == 429
    assert long_calls["n"] == 1


def test_transport_failures_do_not_include_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with client_for(handler) as client:
        with pytest.raises(TransportError, match="timed out") as failure:
            client.list_devices()
    assert TOKEN not in str(failure.value)
    assert TOKEN not in str(failure.value.__cause__)


def test_revoke_token_disables_later_calls() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.method == "DELETE"
        assert request.url.path == "/v2/auth/token"
        return httpx.Response(204)

    with client_for(handler) as client:
        client.revoke_token()
        with pytest.raises(AuthenticationError, match="revoked"):
            client.get_version()
    assert calls["n"] == 1
