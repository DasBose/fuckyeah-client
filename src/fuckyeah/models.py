"""Resources returned by the FuckYeah API, and the pattern values sent to it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from fuckyeah.exceptions import FuckYeahError

# A pattern phase position, speed, or acceleration is a fraction of the device range.
_POSITION_RANGE = (0.0, 1.0)
_SPEED_RANGE = (0.01, 1.0)
_ACCELERATION_RANGE = (0.01, 1.0)
_DELAY_RANGE = (0.0, 5.0)
_PHASE_MIN = 2
_PHASE_MAX = 150
_PATTERN_NAME_MAX = 19
_DEVICE_NAME_MAX = 64


def _required(data: Mapping[str, Any], key: str) -> Any:
    if key not in data:
        raise FuckYeahError(f"Response is missing required field {key!r}")
    return data[key]


def _optional_str(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise FuckYeahError(f"Response field {field!r} is not a string")


def _required_str(data: Mapping[str, Any], key: str) -> str:
    value = _required(data, key)
    if not isinstance(value, str):
        raise FuckYeahError(f"Response field {key!r} is not a string")
    return value


def _required_bool(data: Mapping[str, Any], key: str) -> bool:
    value = _required(data, key)
    if not isinstance(value, bool):
        raise FuckYeahError(f"Response field {key!r} is not a boolean")
    return value


def _parse_datetime(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FuckYeahError(f"Response field {field!r} is not a datetime string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise FuckYeahError(f"Response field {field!r} is not a valid datetime") from exc


def _as_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
    return float(value)


def _check_range(value: Any, field: str, low: float, high: float) -> float:
    number = _as_number(value, field)
    if number < low or number > high:
        raise ValueError(f"{field} must be between {low:g} and {high:g}")
    return number


def validate_pattern_name(name: str) -> str:
    """Return ``name`` when it can be shown on a device screen."""
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    if name == "":
        raise ValueError("name must not be empty")
    if len(name) > _PATTERN_NAME_MAX:
        raise ValueError(f"name must be at most {_PATTERN_NAME_MAX} characters")
    try:
        name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("name must contain only ASCII characters") from exc
    return name


def validate_device_name(name: str) -> str:
    """Return ``name`` when it fits the device rename and registration limit."""
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    if name == "":
        raise ValueError("name must not be empty")
    if len(name) > _DEVICE_NAME_MAX:
        raise ValueError(f"name must be at most {_DEVICE_NAME_MAX} characters")
    return name


@dataclass(frozen=True, slots=True)
class Phase:
    """One step of a pattern.

    ``p`` is position (0 to 1), ``s`` is speed (0.01 to 1), ``a`` is
    acceleration (0.01 to 1), and ``d`` is delay in seconds (0 to 5).
    ``r1`` and ``r2`` switch the two auxiliary relays for this step.
    Speeds and strokes later commanded remotely are multiplied by these
    values and by the device's own settings.
    """

    p: float
    s: float
    a: float
    d: float
    r1: bool
    r2: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "p", _check_range(self.p, "p", *_POSITION_RANGE))
        object.__setattr__(self, "s", _check_range(self.s, "s", *_SPEED_RANGE))
        object.__setattr__(self, "a", _check_range(self.a, "a", *_ACCELERATION_RANGE))
        object.__setattr__(self, "d", _check_range(self.d, "d", *_DELAY_RANGE))
        if not isinstance(self.r1, bool) or not isinstance(self.r2, bool):
            raise TypeError("r1 and r2 must be booleans")

    def to_api(self) -> dict[str, float | bool]:
        return {
            "p": self.p,
            "s": self.s,
            "a": self.a,
            "d": self.d,
            "r1": self.r1,
            "r2": self.r2,
        }

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> Phase:
        missing = [key for key in ("p", "s", "a", "d", "r1", "r2") if key not in data]
        if missing:
            raise FuckYeahError(
                "Response phase is missing " + ", ".join(repr(key) for key in missing)
            )
        try:
            return cls(
                p=data["p"],
                s=data["s"],
                a=data["a"],
                d=data["d"],
                r1=data["r1"],
                r2=data["r2"],
            )
        except (TypeError, ValueError) as exc:
            raise FuckYeahError(f"Response phase is invalid: {exc}") from exc


def phases_to_api(phases: Sequence[Phase | Mapping[str, Any]]) -> list[dict[str, float | bool]]:
    """Validate a pattern's phases and return the JSON list the API expects."""
    if isinstance(phases, (str, bytes)) or not isinstance(phases, Sequence):
        raise TypeError("phases must be a sequence of Phase values")
    count = len(phases)
    if count < _PHASE_MIN or count > _PHASE_MAX:
        raise ValueError(f"phases must contain between {_PHASE_MIN} and {_PHASE_MAX} items")
    encoded: list[dict[str, float | bool]] = []
    for index, phase in enumerate(phases):
        if isinstance(phase, Phase):
            item = phase
        elif isinstance(phase, Mapping):
            try:
                item = Phase(
                    p=phase["p"],
                    s=phase["s"],
                    a=phase["a"],
                    d=phase["d"],
                    r1=phase["r1"],
                    r2=phase["r2"],
                )
            except KeyError as exc:
                raise ValueError(f"phases[{index}] is missing {exc.args[0]!r}") from exc
        else:
            raise TypeError(f"phases[{index}] must be a Phase or a mapping")
        encoded.append(item.to_api())
    return encoded


@dataclass(frozen=True, slots=True)
class Author:
    """A pattern author who has a public profile. Other authors are omitted."""

    id: str
    public_name: str | None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> Author:
        return cls(
            id=_required_str(data, "id"),
            public_name=_optional_str(_required(data, "public_name"), "public_name"),
        )


@dataclass(frozen=True, slots=True)
class Pattern:
    """A pattern you own, a default pattern, or another user's public pattern.

    ``type`` is ``"user"``, ``"default"``, ``"public"``, ``None``, or a value
    added by a later revision of API v2. ``phases`` and ``author`` are ``None``
    when the response did not include them.
    """

    id: str
    name: str
    public: bool
    description: str | None
    type: str | None
    created_at: datetime | None
    updated_at: datetime | None
    phases: tuple[Phase, ...] | None = None
    author: Author | None = None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> Pattern:
        if not isinstance(data, Mapping):
            raise FuckYeahError("Response pattern is not an object")
        phases: tuple[Phase, ...] | None = None
        if "phases" in data and data["phases"] is not None:
            raw_phases = data["phases"]
            if not isinstance(raw_phases, list):
                raise FuckYeahError("Response field 'phases' is not an array")
            phases = tuple(Phase.from_api(item) for item in raw_phases)
        author: Author | None = None
        if "author" in data and data["author"] is not None:
            raw_author = data["author"]
            if not isinstance(raw_author, Mapping):
                raise FuckYeahError("Response field 'author' is not an object")
            author = Author.from_api(raw_author)
        pattern_type = _required(data, "type")
        if pattern_type is not None and not isinstance(pattern_type, str):
            raise FuckYeahError("Response field 'type' is not a string")
        return cls(
            id=_required_str(data, "id"),
            name=_required_str(data, "name"),
            public=_required_bool(data, "public"),
            description=_optional_str(_required(data, "description"), "description"),
            type=pattern_type,
            created_at=_parse_datetime(_required(data, "created_at"), "created_at"),
            updated_at=_parse_datetime(_required(data, "updated_at"), "updated_at"),
            phases=phases,
            author=author,
        )


@dataclass(frozen=True, slots=True)
class PatternPage:
    """One cursor page of patterns. Pages contain 20 patterns, ordered by name."""

    patterns: tuple[Pattern, ...]
    per_page: int
    next_cursor: str | None
    prev_cursor: str | None
    next_url: str | None = None
    prev_url: str | None = None

    @property
    def has_next(self) -> bool:
        return self.next_cursor is not None

    @property
    def has_prev(self) -> bool:
        return self.prev_cursor is not None


@dataclass(frozen=True, slots=True)
class Device:
    """A machine registered to the authenticated account.

    ``patterns`` is the patterns loaded on the device, in load order, when the
    response included them. Registration can return a device without that list.
    """

    id: str
    thing_name: str
    serial_number: str
    name: str | None
    beta: bool
    last_connection_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
    last_sync_at: datetime | None
    hardware_model: str | None
    hardware_version: int | float | None
    firmware_version: str | None
    firmware_target_version: str | None
    patterns: tuple[Pattern, ...] | None = None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> Device:
        if not isinstance(data, Mapping):
            raise FuckYeahError("Response device is not an object")
        hardware_version = _required(data, "hardware_version")
        if hardware_version is not None and (
            isinstance(hardware_version, bool) or not isinstance(hardware_version, (int, float))
        ):
            raise FuckYeahError("Response field 'hardware_version' is not a number")
        patterns: tuple[Pattern, ...] | None = None
        if "patterns" in data and data["patterns"] is not None:
            raw_patterns = data["patterns"]
            if not isinstance(raw_patterns, list):
                raise FuckYeahError("Response field 'patterns' is not an array")
            patterns = tuple(Pattern.from_api(item) for item in raw_patterns)
        return cls(
            id=_required_str(data, "id"),
            thing_name=_required_str(data, "thing_name"),
            serial_number=_required_str(data, "serial_number"),
            name=_optional_str(_required(data, "name"), "name"),
            beta=_required_bool(data, "beta"),
            last_connection_at=_parse_datetime(
                _required(data, "last_connection_at"), "last_connection_at"
            ),
            created_at=_parse_datetime(_required(data, "created_at"), "created_at"),
            updated_at=_parse_datetime(_required(data, "updated_at"), "updated_at"),
            last_sync_at=_parse_datetime(_required(data, "last_sync_at"), "last_sync_at"),
            hardware_model=_optional_str(_required(data, "hardware_model"), "hardware_model"),
            hardware_version=hardware_version,
            firmware_version=_optional_str(
                _required(data, "firmware_version"), "firmware_version"
            ),
            firmware_target_version=_optional_str(
                _required(data, "firmware_target_version"), "firmware_target_version"
            ),
            patterns=patterns,
        )


@dataclass(frozen=True, slots=True)
class MinAppVersions:
    """Oldest native app versions still allowed to use this API.

    The live API returns an object with ``ios`` and ``android``. The published
    schema types the same field as a string; that string is preserved on
    ``raw`` and the platform fields stay ``None``. Platform keys other than
    ``ios`` and ``android`` are ignored.
    """

    ios: str | None = None
    android: str | None = None
    raw: str | None = None

    @classmethod
    def from_api(cls, value: Any) -> MinAppVersions:
        if isinstance(value, str):
            return cls(raw=value)
        if isinstance(value, Mapping):
            return cls(
                ios=_optional_str(value.get("ios"), "min_app_versions.ios"),
                android=_optional_str(value.get("android"), "min_app_versions.android"),
            )
        raise FuckYeahError("Response field 'min_app_versions' has an unexpected type")


@dataclass(frozen=True, slots=True)
class ApiVersion:
    """API version and the oldest supported native app versions."""

    version: str
    min_app_versions: MinAppVersions

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> ApiVersion:
        if not isinstance(data, Mapping):
            raise FuckYeahError("Response version is not an object")
        version = _required(data, "version")
        if not isinstance(version, str):
            raise FuckYeahError("Response field 'version' is not a string")
        return cls(
            version=version,
            min_app_versions=MinAppVersions.from_api(_required(data, "min_app_versions")),
        )


@dataclass(frozen=True, slots=True)
class RateLimit:
    """Rate-limit counters from the most recent response, when the API sent them."""

    limit: int | None
    remaining: int | None
