"""Opt-in inverse assertion helper for negative-path test suites."""

from __future__ import annotations

from ..assertions import BackendFactory, GateAssertionError, _resolve_backend
from ..backends import Backend
from ..backends.memory import MemoryBackend
from ..engine.gate import evaluate
from ..engine.gate_values import GatePolicy


def assert_gate_violated(
    spec: dict,
    *,
    backend: Backend | None = None,
    backend_factory: BackendFactory = MemoryBackend,
    ontology=None,
    policy: GatePolicy | None = None,
    strict_infra: bool = False,
) -> dict:
    """Raise unless a reachable, complete evaluation violates ``spec``."""

    resolved = _resolve_backend(backend, backend_factory)
    result = evaluate(resolved, spec, ontology=ontology, policy=policy)
    if not result["reachable"] or not result.get("complete", True):
        if strict_infra:
            raise GateAssertionError(
                f"cannot confirm violation for cid={result['cid']}: evidence is inconclusive"
            )
        return result
    if result["ok"]:
        raise GateAssertionError(f"expected a violation for cid={result['cid']}")
    return result


__all__ = ["assert_gate_violated"]
