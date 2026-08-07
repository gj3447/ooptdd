"""Immutable runtime configuration values.

This module owns *values*, not configuration I/O.  It never reads a file or the
process environment.  The outer adapters in :mod:`ooptdd.config` resolve source
precedence once and hand the resulting snapshot to whichever adapters and
engine entry points the application composes.

Protocol constants (schema versions, digest domains, FSM states) deliberately do
not live here: applications may tune runtime settings, but must not be able to
silently rewrite a wire or cryptographic contract.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

DEFAULT_BACKEND = "memory"
DEFAULT_SERVICE = "ooptdd"
DEFAULT_CID_ENV = "OOPTDD_CID"

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class EnvironmentKeys:
    """Canonical names for every environment-owned runtime input.

    The names are data so adapters can be tested with an ordinary mapping and
    installations can replace the key set without editing engine code.
    """

    backend: str = "OOPTDD_BACKEND"
    service: str = "OOPTDD_SERVICE"
    extensions: str = "OOPTDD_EXTENSIONS"
    cid: str = DEFAULT_CID_ENV
    retries: str = "OOPTDD_RETRIES"
    delay: str = "OOPTDD_DELAY"
    backoff: str = "OOPTDD_BACKOFF"
    max_delay: str = "OOPTDD_MAX_DELAY"
    confirm_rounds: str = "OOPTDD_CONFIRM_ROUNDS"
    confirm_delay_s: str = "OOPTDD_CONFIRM_DELAY"

    signing_key: str = "OOPTDD_SIGNING_KEY"
    require_signature: str = "OOPTDD_REQUIRE_SIGNATURE"
    forbid_errors: str = "OOPTDD_FORBID_ERRORS"
    require_corroboration: str = "OOPTDD_REQUIRE_CORROBORATION"
    require_independent_store: str = "OOPTDD_REQUIRE_INDEPENDENT"

    adapter_verify: str = "OOPTDD_VERIFY"
    adapter_enabled: str = "OOPTDD_ENABLED"
    adapter_cid_env: str = "OOPTDD_CID_ENV"

    openobserve_url: str = "OOPTDD_OO_URL"
    openobserve_user: str = "OOPTDD_OO_USER"
    openobserve_password: str = "OOPTDD_OO_PASSWORD"
    openobserve_org: str = "OOPTDD_OO_ORG"
    clickhouse_url: str = "OOPTDD_CH_URL"
    clickhouse_user: str = "OOPTDD_CH_USER"
    clickhouse_password: str = "OOPTDD_CH_PASSWORD"
    clickhouse_database: str = "OOPTDD_CH_DATABASE"
    victorialogs_url: str = "OOPTDD_VL_URL"
    victorialogs_user: str = "OOPTDD_VL_USER"
    victorialogs_password: str = "OOPTDD_VL_PASSWORD"
    jsonl_path: str = "OOPTDD_JSONL_PATH"
    otel_endpoint: str = "OTEL_EXPORTER_OTLP_ENDPOINT"


DEFAULT_ENV_KEYS = EnvironmentKeys()


def _freeze_setting_value(value: object, active: set[int] | None = None) -> object:
    """Capture JSON-shaped configuration without retaining mutable caller aliases."""

    if value is None or isinstance(value, str | bytes | bool | int | float):
        return value
    seen = set() if active is None else active
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise ValueError("backend_options cannot contain a cycle")
        seen.add(identity)
        try:
            captured: dict[str, object] = {}
            for key, child in value.items():
                if not isinstance(key, str) or not key:
                    raise TypeError("backend_options keys must be non-empty strings")
                captured[key] = _freeze_setting_value(child, seen)
            return MappingProxyType(captured)
        finally:
            seen.remove(identity)
    if isinstance(value, list | tuple):
        identity = id(value)
        if identity in seen:
            raise ValueError("backend_options cannot contain a cycle")
        seen.add(identity)
        try:
            return tuple(_freeze_setting_value(child, seen) for child in value)
        finally:
            seen.remove(identity)
    raise TypeError(
        "backend_options values must be immutable scalars, mappings, or sequences; "
        f"got {type(value).__name__}"
    )


def _finite_non_negative(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number, got {value!r}")


@dataclass(frozen=True)
class PollingSettings:
    """One immutable policy for every arrival polling entry point."""

    retries: int = 4
    delay: float = 1.0
    backoff: float = 2.0
    max_delay: float = 30.0
    confirm_rounds: int = 0
    confirm_delay_s: float = 1.0

    def __post_init__(self) -> None:
        if isinstance(self.retries, bool) or self.retries < 1:
            raise ValueError(f"retries must be an integer >= 1, got {self.retries!r}")
        if isinstance(self.confirm_rounds, bool) or self.confirm_rounds < 0:
            raise ValueError(f"confirm_rounds must be an integer >= 0, got {self.confirm_rounds!r}")
        _finite_non_negative("delay", float(self.delay))
        _finite_non_negative("backoff", float(self.backoff))
        _finite_non_negative("max_delay", float(self.max_delay))
        _finite_non_negative("confirm_delay_s", float(self.confirm_delay_s))


DEFAULT_POLLING = PollingSettings()


@dataclass(frozen=True)
class SigningSettings:
    """Ephemeral signing material resolved at a composition boundary.

    It is kept separate from :class:`FrameworkSettings` so a normal settings repr or
    serialized diagnostic cannot accidentally disclose the key.
    """

    key: str | None = field(default=None, repr=False)
    require_signature: bool = False


@dataclass(frozen=True)
class FrameworkSettings:
    """Resolved, immutable application settings.

    Flat polling accessors remain for source compatibility; ``polling`` is the
    policy object passed through new code.  Defaults are referenced from
    :data:`DEFAULT_POLLING`, so there is only one owner for their values.
    """

    backend: str = DEFAULT_BACKEND
    service: str = DEFAULT_SERVICE
    retries: int = DEFAULT_POLLING.retries
    delay: float = DEFAULT_POLLING.delay
    backoff: float = DEFAULT_POLLING.backoff
    max_delay: float = DEFAULT_POLLING.max_delay
    confirm_rounds: int = DEFAULT_POLLING.confirm_rounds
    confirm_delay_s: float = DEFAULT_POLLING.confirm_delay_s
    extensions: tuple[str, ...] = ()
    backend_options: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for name in ("backend", "service"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be a non-empty string")
        polling = self.polling
        object.__setattr__(self, "retries", polling.retries)
        object.__setattr__(self, "delay", polling.delay)
        object.__setattr__(self, "backoff", polling.backoff)
        object.__setattr__(self, "max_delay", polling.max_delay)
        object.__setattr__(self, "confirm_rounds", polling.confirm_rounds)
        object.__setattr__(self, "confirm_delay_s", polling.confirm_delay_s)
        normalized_extensions: list[str] = []
        raw_extensions = (
            self.extensions.split(",") if isinstance(self.extensions, str) else self.extensions
        )
        for extension in raw_extensions:
            name = str(extension).strip()
            if not name:
                raise ValueError("extensions must contain non-empty provider names")
            if name not in normalized_extensions:
                normalized_extensions.append(name)
        object.__setattr__(self, "extensions", tuple(normalized_extensions))
        frozen_options = _freeze_setting_value(self.backend_options)
        if not isinstance(frozen_options, Mapping):  # defensive: field type is validated above
            raise TypeError("backend_options must be a mapping")
        object.__setattr__(self, "backend_options", frozen_options)

    @property
    def polling(self) -> PollingSettings:
        return PollingSettings(
            retries=int(self.retries),
            delay=float(self.delay),
            backoff=float(self.backoff),
            max_delay=float(self.max_delay),
            confirm_rounds=int(self.confirm_rounds),
            confirm_delay_s=float(self.confirm_delay_s),
        )


# Compatibility name for consumers that imported the old generic settings type.
Settings = FrameworkSettings


__all__ = [
    "DEFAULT_BACKEND",
    "DEFAULT_CID_ENV",
    "DEFAULT_ENV_KEYS",
    "DEFAULT_POLLING",
    "DEFAULT_SERVICE",
    "EnvironmentKeys",
    "FALSE_VALUES",
    "FrameworkSettings",
    "PollingSettings",
    "Settings",
    "SigningSettings",
    "TRUE_VALUES",
]
