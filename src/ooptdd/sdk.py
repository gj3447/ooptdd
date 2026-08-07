"""Stable composition surface for independently distributed OOPTDD extensions.

Extensions should import these names instead of reaching into ``ooptdd.engine`` or
``ooptdd.bootstrap`` implementation modules.  The version constant describes this
surface independently of the package version so a future provider-discovery adapter
can reject incompatible plugins before composition.
"""

from __future__ import annotations

from .bootstrap import ExtensionProvider, Runtime, compose_runtime
from .domain.ports import BackendCaps, ProbeResult, QueryResult, backend_caps
from .engine.gate import (
    CheckRegistry,
    check,
    checks_from,
    compose_check_registry,
    evaluate_events,
    evidence_tier,
    load_gate,
    matches_event,
    resolve_gate_policy,
    resolve_matcher,
)
from .engine.gate_rules import detect_check_key
from .engine.gate_values import CheckCtx as CheckContext
from .engine.gate_values import CheckFn, GatePolicy
from .engine.monitor import compile_check
from .engine.verify import verify_gate
from .reports import to_junit_xml, to_markdown

EXTENSION_API_VERSION = 1

__all__ = (
    "EXTENSION_API_VERSION",
    "BackendCaps",
    "CheckContext",
    "CheckFn",
    "CheckRegistry",
    "ExtensionProvider",
    "GatePolicy",
    "ProbeResult",
    "QueryResult",
    "Runtime",
    "backend_caps",
    "check",
    "checks_from",
    "compile_check",
    "compose_check_registry",
    "compose_runtime",
    "detect_check_key",
    "evidence_tier",
    "evaluate_events",
    "load_gate",
    "matches_event",
    "resolve_gate_policy",
    "resolve_matcher",
    "to_junit_xml",
    "to_markdown",
    "verify_gate",
)
