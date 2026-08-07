"""ooptdd — a general event-contract verification framework.

The core accepts arbitrary structured events, backend ports, gate specifications,
and explicit runtime policies. Domain vocabularies and specialized checks are
available through explicitly selected extension providers.

Public API:
    build_event             generic event-envelope constructor
    verify_gate             poll until an arbitrary gate spec arrives for a cid (generic)
    poll_until_present      the generic, shape-agnostic arrival loop underneath both
    evaluate, evaluate_events   run a gate spec (read+judge) / judge already-fetched events
    check                   metadata decorator for explicitly composed custom predicates
    compile_check, LiveMonitorSet   kernel API: rule -> Monitor -> feed a live stream
    get_backend, BackendRegistry    resolve a Backend driver by name
    QuerySpec, TimeWindow, BackendCaps, Clock, SystemClock   the domain ports/value objects
    Backend, QueryResult
"""

from .backends import (
    Backend,
    BackendCaps,
    BackendRegistry,
    Clock,
    QueryResult,
    QuerySpec,
    SystemClock,
    TimeWindow,
    get_backend,
)
from .domain.model import (
    Emitter,
    build_event,
    correlation_keys,
    sign_chain,
    verify_chain,
)
from .domain.ontology import EventType, Ontology, check_conformance
from .domain.ports import ExternalProbe, ProbeResult
from .engine.gate import (
    EVIDENCE_TIERS,
    check,
    checks_from,
    combine_results,
    compare_strength,
    compose_check_registry,
    evaluate,
    evaluate_events,
    evidence_tier,
    failed_checks,
    lint_spec,
    load_gate,
    strength_fingerprint,
)
from .engine.monitor import LiveMonitorSet, compile_check
from .engine.verify import poll_until_present, verify_gate
from .probes import CallableProbe, ProbeRegistry, get_probe

__all__ = [
    "build_event",
    "Emitter",
    "correlation_keys",
    "verify_gate",
    "poll_until_present",
    "get_backend",
    "BackendRegistry",
    "load_gate",
    "evaluate",
    "evaluate_events",
    "evidence_tier",
    "EVIDENCE_TIERS",
    "lint_spec",
    "strength_fingerprint",
    "compare_strength",
    "sign_chain",
    "verify_chain",
    "check",
    "checks_from",
    "compose_check_registry",
    "combine_results",
    "failed_checks",
    "compile_check",
    "LiveMonitorSet",
    "Backend",
    "QueryResult",
    "QuerySpec",
    "TimeWindow",
    "BackendCaps",
    "Clock",
    "SystemClock",
    "Ontology",
    "EventType",
    "check_conformance",
    "ExternalProbe",
    "ProbeResult",
    "CallableProbe",
    "ProbeRegistry",
    "get_probe",
]

__version__ = "0.6.0"
