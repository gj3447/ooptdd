"""Compatibility exports for verification APIs.

Generic polling and gate verification are eager. Pytest session helpers are lazy deprecated
exports from :mod:`ooptdd.adapters.pytest`, keeping normal imports framework-neutral.
"""

from __future__ import annotations

import warnings

from .engine.verify import poll_until_present, verify_gate  # noqa: F401

_PYTEST_COMPAT = frozenset({"session_finish", "verify_policy", "verify_trace"})


def __getattr__(name: str):
    if name not in _PYTEST_COMPAT:
        raise AttributeError(name)
    warnings.warn(
        f"ooptdd.verify.{name} moved to ooptdd.adapters.pytest",
        DeprecationWarning,
        stacklevel=2,
    )
    from .adapters import pytest as pytest_adapter

    return getattr(pytest_adapter, name)
