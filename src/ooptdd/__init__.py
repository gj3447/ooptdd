"""ooptdd — logs/traces as the test specification and ground truth.

ooptdd (also written LTDD, "Log-based / observability positive-TDD") inverts the
trust relationship of a normal test: instead of believing a function's return
value (or an agent's "done!"), it asserts that the *events the system actually
emitted* arrived in an external store.

    Red      write the expected event-trace spec (it fails — nothing emits it yet)
    Green    the implementation emits structured events; a verifier polls the
             store and *positively asserts they arrived*
    Refactor the same event contract still holds (golden-trace regression)

The word **positive** is load-bearing: `ship()` returning without raising is a
*claim*, not proof. A separate verifier reads the store back and asserts the
records exist. (Motivating failure: a silent 401 dropped ingest for 22 hours and
every "shipped OK" log lied.)

Public API:
    build_outcome_records   pure: pytest reports -> structured event records
    verify_trace            poll a backend, return an LTL3 verdict
    verify_policy           verdict + mode -> build decision
    session_finish          build -> ship -> verify -> policy (the orchestrator)
    get_backend             resolve a Backend driver by name
    evaluate, can_i_deploy  run a gate spec / Pact-style multi-gate deploy decision
    check                   @decorator to register a custom gate check-predicate (seam)
    assert_gate, assert_present   DeepEval-style in-test trace assertions
    Backend, QueryResult, MemoryBackend
"""
from .assertions import TraceAssertionError, assert_gate, assert_present
from .backends import Backend, MemoryBackend, QueryResult, get_backend
from .domain import (
    semconv as _semconv,  # noqa: F401  # registers the "gen_ai" builtin preset (Ontology.register_preset)
)
from .domain.model import build_outcome_records
from .domain.ontology import EventType, Ontology, check_conformance
from .engine.gate import can_i_deploy, check, evaluate
from .engine.verify import session_finish, verify_policy, verify_trace

__all__ = [
    "build_outcome_records",
    "verify_trace",
    "verify_policy",
    "session_finish",
    "get_backend",
    "evaluate",
    "can_i_deploy",
    "check",
    "assert_gate",
    "assert_present",
    "TraceAssertionError",
    "Backend",
    "QueryResult",
    "MemoryBackend",
    "Ontology",
    "EventType",
    "check_conformance",
]

__version__ = "0.2.0"
