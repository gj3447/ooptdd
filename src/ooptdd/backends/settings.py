"""Immutable operational defaults shared by network backend adapters.

These values bound transport operations; they do not define event meaning or verdict
policy. Applications may inject a complete settings value and may still override one
constructor argument for a particular adapter instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class BackendSettings:
    """Resource and time bounds for network-backed adapters."""

    lookback_s: int = 3_600
    future_buffer_s: int = 300
    timeout_s: float = 15.0
    page_size: int = 1_000
    max_rows: int = 1_000_000

    def __post_init__(self) -> None:
        for field_name in ("lookback_s", "future_buffer_s"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"backend {field_name} must be a non-negative integer")
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, int | float)
            or not isfinite(self.timeout_s)
            or self.timeout_s <= 0
        ):
            raise ValueError("backend timeout_s must be finite and positive")
        for field_name in ("page_size", "max_rows"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"backend {field_name} must be a positive integer")
        if self.page_size > self.max_rows:
            raise ValueError("backend page_size must not exceed max_rows")

    def timeout(self, override: float | None = None) -> float:
        """Resolve and validate a per-instance timeout override."""

        value = self.timeout_s if override is None else override
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not isfinite(value)
            or value <= 0
        ):
            raise ValueError("backend timeout must be finite and positive")
        return float(value)

    def row_limit(self, override: int | None = None) -> int:
        """Resolve and validate a per-instance maximum-row override."""

        value = self.max_rows if override is None else override
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("backend max_rows must be a positive integer")
        return value

    def page_limit(self, override: int | None = None) -> int:
        """Resolve and validate a per-instance page-size override."""

        value = self.page_size if override is None else override
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("backend page_size must be a positive integer")
        return value


DEFAULT_BACKEND_SETTINGS = BackendSettings()
DEFAULT_LOCAL_BACKEND_SETTINGS = BackendSettings(future_buffer_s=0)


__all__ = (
    "BackendSettings",
    "DEFAULT_BACKEND_SETTINGS",
    "DEFAULT_LOCAL_BACKEND_SETTINGS",
)
