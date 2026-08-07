"""Compatibility exports for generic event-model primitives.

Pytest builders moved to :mod:`ooptdd.adapters.pytest`. They are resolved lazily only
when legacy callers request those names, so importing this module remains generic.
"""

from __future__ import annotations

import warnings

from .domain.model import (  # noqa: F401
    ENVELOPE_SCHEMA,
    ENVELOPE_SPEC_VERSION,
    SIG_ALG,
    cloudevents_envelope,
    correlation_keys,
    sign_chain,
    sign_record,
    signature_status,
    validate_cloudevents,
    verify_chain,
    with_trace_context,
)

_PYTEST_COMPAT = frozenset({"build_outcome_records", "build_session_start"})


def __getattr__(name: str):
    if name not in _PYTEST_COMPAT:
        raise AttributeError(name)
    warnings.warn(
        f"ooptdd.model.{name} moved to ooptdd.adapters.pytest",
        DeprecationWarning,
        stacklevel=2,
    )
    from .adapters import pytest as pytest_adapter

    return getattr(pytest_adapter, name)
