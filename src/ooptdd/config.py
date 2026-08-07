"""Runtime configuration boundary.

The pure resolver implements one precedence law::

    built-in defaults < project mapping < environment mapping < caller overrides

Only the compatibility wrappers :func:`from_mapping` and :func:`load_pyproject`
touch ambient I/O.  The engine, backends and resolved settings object do not.
Secrets remain environment-owned and are resolved into a separate, non-printing
value object rather than the ordinary application settings.
"""

from __future__ import annotations

import os
import sys
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, cast

from .domain.settings import (
    DEFAULT_ENV_KEYS,
    FALSE_VALUES,
    TRUE_VALUES,
    EnvironmentKeys,
    FrameworkSettings,
    SigningSettings,
)

Settings = FrameworkSettings


def parse_bool(value: object, default: bool = False) -> bool:
    """Parse the one boolean convention used by every ooptdd adapter."""

    if value is None:
        return default
    normalized = str(value).strip().lower()
    if not normalized:
        return default
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    warnings.warn(
        f"ooptdd: unrecognized boolean {value!r}; using default {default}",
        stacklevel=2,
    )
    return default


def _as_text(value: object) -> str:
    return str(value).strip()


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError(f"expected an integer, got boolean {value!r}")
    return int(str(value))


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError(f"expected a number, got boolean {value!r}")
    return float(str(value))


def _as_extensions(value: object) -> tuple[str, ...]:
    """Normalize named extension providers without importing anything."""

    if isinstance(value, str):
        candidates: tuple[object, ...] = tuple(value.split(","))
    elif isinstance(value, (list, tuple)):
        candidates = tuple(value)
    else:
        raise TypeError("expected a comma-separated string or a list of provider names")
    extensions = tuple(str(item).strip() for item in candidates if str(item).strip())
    if not extensions and value:
        raise ValueError("extension list contains no provider names")
    return extensions


@dataclass(frozen=True)
class SettingDefinition:
    """Declarative ownership for one scalar setting across project and environment."""

    field: str
    env_attr: str
    converter: Callable[[object], object]
    help: str


SETTING_DEFINITIONS = (
    SettingDefinition("backend", "backend", _as_text, "backend name or entry point"),
    SettingDefinition("service", "service", _as_text, "service name stamped on events"),
    SettingDefinition(
        "extensions",
        "extensions",
        _as_extensions,
        "comma-separated named extension providers",
    ),
    SettingDefinition("retries", "retries", _as_int, "arrival-poll attempts (integer >= 1)"),
    SettingDefinition("delay", "delay", _as_float, "initial arrival-poll delay in seconds"),
    SettingDefinition("backoff", "backoff", _as_float, "arrival-poll backoff multiplier"),
    SettingDefinition("max_delay", "max_delay", _as_float, "maximum poll delay in seconds"),
    SettingDefinition(
        "confirm_rounds",
        "confirm_rounds",
        _as_int,
        "anti-flap confirmation reads after a revocable satisfaction",
    ),
    SettingDefinition(
        "confirm_delay_s",
        "confirm_delay_s",
        _as_float,
        "delay between anti-flap confirmation reads",
    ),
)

_DEFINITION_BY_FIELD: Mapping[str, SettingDefinition] = MappingProxyType(
    {item.field: item for item in SETTING_DEFINITIONS}
)
_PROJECT_FIELDS = frozenset({*_DEFINITION_BY_FIELD, "backend_options"})
_ADAPTER_NAMESPACE = "adapters"


def _default_values() -> dict[str, object]:
    defaults = FrameworkSettings()
    return {item.field: getattr(defaults, item.field) for item in SETTING_DEFINITIONS} | {
        "backend_options": dict(defaults.backend_options)
    }


def _validate_source(name: str, raw: Mapping[str, object] | None) -> Mapping[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise TypeError(f"{name} settings must be a mapping, got {type(raw).__name__}")
    allowed = _PROJECT_FIELDS | ({_ADAPTER_NAMESPACE} if name == "project" else set())
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise ValueError(
            f"unknown ooptdd {name} setting(s): {unknown}; "
            "backend-specific values belong under backend_options"
        )
    adapters = raw.get(_ADAPTER_NAMESPACE)
    if adapters is not None and not isinstance(adapters, Mapping):
        raise TypeError("project.adapters must be a table")
    return {key: value for key, value in raw.items() if key in _PROJECT_FIELDS}


def _apply_mapping(
    values: dict[str, object],
    raw: Mapping[str, object],
    *,
    source: str,
) -> None:
    for key, value in raw.items():
        if value is None:
            continue
        if key == "backend_options":
            if not isinstance(value, Mapping):
                raise TypeError(f"{source}.backend_options must be a mapping")
            values[key] = dict(value)
            continue
        definition = _DEFINITION_BY_FIELD[key]
        try:
            values[key] = definition.converter(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid {source} setting {key}={value!r}: {error}") from error


def resolve_settings(
    project: Mapping[str, object] | None = None,
    environment: Mapping[str, str] | None = None,
    overrides: Mapping[str, object] | None = None,
    *,
    env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
) -> FrameworkSettings:
    """Purely resolve a frozen settings snapshot from explicit input mappings."""

    project_values = _validate_source("project", project)
    override_values = _validate_source("override", overrides)
    environment = {} if environment is None else environment
    if not isinstance(environment, Mapping):
        raise TypeError("environment must be a mapping")

    values = _default_values()
    _apply_mapping(values, project_values, source="project")
    for definition in SETTING_DEFINITIONS:
        env_name = getattr(env_keys, definition.env_attr)
        if env_name in environment:
            try:
                values[definition.field] = definition.converter(environment[env_name])
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"invalid environment setting {env_name}={environment[env_name]!r}: {error}"
                ) from error
    _apply_mapping(values, override_values, source="override")
    return FrameworkSettings(**cast(dict[str, Any], values))


def resolve_signing_settings(
    environment: Mapping[str, str],
    *,
    env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
) -> SigningSettings:
    """Resolve signing intent once without storing the secret in ordinary settings."""

    key = environment.get(env_keys.signing_key) or None
    declared = environment.get(env_keys.require_signature)
    if declared is None or not declared.strip():
        required = bool(key)
    else:
        normalized = declared.strip().lower()
        if normalized not in TRUE_VALUES | FALSE_VALUES:
            raise ValueError(f"invalid security boolean {env_keys.require_signature}={declared!r}")
        required = normalized in TRUE_VALUES
    return SigningSettings(key=key, require_signature=required)


def from_mapping(
    table: Mapping[str, object] | None,
    *,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, object] | None = None,
    env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
) -> FrameworkSettings:
    """Compatibility shell around :func:`resolve_settings`.

    Omitting ``environ`` captures the process environment exactly once.  New
    code should pass an explicit mapping or call the pure resolver directly.
    """

    captured = dict(os.environ) if environ is None else environ
    return resolve_settings(table, captured, overrides, env_keys=env_keys)


def with_overrides(settings: FrameworkSettings, **overrides: Any) -> FrameworkSettings:
    """Return a changed immutable snapshot with validated field ownership."""

    unknown = sorted(set(overrides) - _PROJECT_FIELDS)
    if unknown:
        raise ValueError(f"unknown settings override(s): {unknown}")
    changes = {key: value for key, value in overrides.items() if value is not None}
    return replace(settings, **changes)


def load_pyproject(path: str = "pyproject.toml") -> dict[str, object]:
    """Read ``[tool.ooptdd]`` from a pyproject file (``{}`` when absent)."""

    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - exercised by the Python 3.10 CI lane
        import tomli as tomllib
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return {}
    table = data.get("tool", {}).get("ooptdd", {})
    if not isinstance(table, dict):
        raise TypeError("[tool.ooptdd] must be a TOML table")
    return table


__all__ = [
    "SETTING_DEFINITIONS",
    "SettingDefinition",
    "FrameworkSettings",
    "Settings",
    "from_mapping",
    "load_pyproject",
    "parse_bool",
    "resolve_settings",
    "resolve_signing_settings",
    "with_overrides",
]
