# fuckyeah

Python client for the [FuckYeah API](https://api.fuckyeah.uk/docs). It controls your devices and manages patterns from scripts and projects.

The client speaks API v2 (`https://api.fuckyeah.uk/v2`). Create a `FuckYeahClient` with a personal API token and the token is sent on every request as `Authorization: Bearer fy_...`.

## Install

```bash
pip install fuckyeah
```

Requires Python 3.10 or newer.

## Authentication

Create a token in the app. Choose a name, the scopes it needs, and, optionally, when it expires. The token is shown once and starts with `fy_`.

```python
import os

from fuckyeah import FuckYeahClient

with FuckYeahClient(os.environ["FUCKYEAH_TOKEN"]) as client:
    for device in client.list_devices():
        print(device.id, device.name)
```

`FuckYeahClient` checks that the token is shaped like a personal token. The API does not provide a way to inspect a token without calling a scoped endpoint, so a rejected, expired, or revoked token raises `AuthenticationError` on the first protected call.

Give each token only the scopes it needs.

| Scope | Allows |
| --- | --- |
| `devices:read` | List and view devices. |
| `devices:manage` | Add, rename, and remove devices. |
| `devices:control` | Send commands and load patterns onto devices. |
| `patterns:read` | List and view patterns. |
| `patterns:write` | Create, update, and delete your patterns. |

A request without the required scope returns HTTP 403.

## Devices

```python
device = client.register_device("Desk", pin=1234)
client.rename_device(device, "Window")

client.load_patterns(device, [pattern_id])
client.run_pattern(device, pattern_id=pattern_id, speed=0.5, r1="pattern", r2="off")
client.stop_device(device)

client.delete_device(device)
```

`register_device` claims a connected machine with the PIN on its screen. The PIN rotates, so register it soon after reading it.

`run_pattern` and `stop_device` return when the API accepts the command (HTTP 202). The device does not confirm that it applied the command. It has to be online, with remote control enabled. Speed and stroke values are fractions of the room left by the device's own settings and the active pattern phase. After a stop, the speed stays at 0 and both relays stay off until the next `run_pattern` sends new values.

Deleting a device detaches its patterns. The patterns remain available to your other devices, and the machine has to be registered again before it can be used online.

## Patterns

```python
from fuckyeah import Phase

pattern = client.create_pattern(
    "Pulse",
    [
        Phase(p=0.2, s=0.4, a=0.5, d=0.0, r1=False, r2=False),
        Phase(p=0.8, s=0.7, a=0.5, d=0.2, r1=True, r2=False),
    ],
    description="A short pulse",
    public=False,
)

for item in client.iter_patterns(types=["user", "default"], query="pulse"):
    print(item.id, item.name, item.type)

client.update_pattern(pattern, public=True)
client.delete_pattern(pattern)
```

A pattern name is ASCII and at most 19 characters so it fits on the device screen. A pattern has 2 to 150 phases. In a phase, `p` is position (0 to 1), `s` is speed (0.01 to 1), `a` is acceleration (0.01 to 1), `d` is delay in seconds (0 to 5), and `r1` / `r2` switch the auxiliary relays.

`list_patterns` returns one page of 20, ordered by name. Pass `cursor=page.next_cursor` to continue, or use `iter_patterns` to follow the cursors. `types` accepts `user` (yours), `public`, `default`, or a combination. `query` keeps names that contain that text.

`type` on a returned pattern is `user`, `default`, `public`, or a value added later. Unknown values are left as they arrived. `author` is set only when that person has a public profile.

Changing anything other than `public` creates a new pattern revision so a device can tell that it should reload. Pass `description=None` to clear a description. You can delete only your own patterns; copies other people made are left alone.

## Tokens

```python
client.revoke_token()
```

This calls `DELETE /auth/token` and then refuses further requests on this client. You can also revoke a token from the app.

## Errors

| Exception | When |
| --- | --- |
| `AuthenticationError` | HTTP 401. The token is missing, invalid, expired, or revoked. |
| `AuthorizationError` | HTTP 403. The token lacks the scope, or the account email is not verified. |
| `NotFoundError` | HTTP 404. |
| `ValidationError` | HTTP 422. `errors` maps each field to the messages returned for it. |
| `RateLimitError` | HTTP 429. `retry_after` is the `Retry-After` delay in seconds, when the API sent one. |
| `TransportError` | The connection failed or timed out before a response arrived. |
| `FuckYeahError` | Any other client or response failure. The exceptions above are subclasses of this. |

HTTP 429 is retried up to `max_retries` times (default 2) when `Retry-After` is 60 seconds or less. A longer delay is raised immediately as `RateLimitError` so the caller can decide whether to wait. Pass `max_retries=0` to disable retries.

`client.rate_limit` holds the `x-ratelimit-limit` and `x-ratelimit-remaining` values from the most recent response, when those headers were present.

Redirects are not followed, so the bearer token stays on the host in `base_url`.

## Compatibility

Within `/v2`, the API only adds fields, endpoints, and enum values. This client ignores response fields it does not recognise. The published schema types `min_app_versions` as a string; the live API returns an object with `ios` and `android`. `get_version()` accepts both.

Breaking changes will arrive as a new API version. Point `base_url` at that version when you are ready to move.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The version in `src/fuckyeah/_version.py` is the version published to PyPI. Build and upload with your usual tools:

```bash
python -m build
twine upload dist/*
```

Support for the API itself is [support@fuckyeah.uk](mailto:support@fuckyeah.uk).
