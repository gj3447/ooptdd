"""Domain-neutral assertion helpers over event-contract evaluations."""

from __future__ import annotations

from collections.abc import Callable

from .backends import Backend
from .backends.memory import MemoryBackend
from .engine.gate import _label, evaluate, failed_checks
from .engine.gate_values import GatePolicy

BackendFactory = Callable[[], Backend]


def _resolve_backend(
    backend: Backend | None,
    backend_factory: BackendFactory,
) -> Backend:
    """Resolve the explicit adapter or construct the documented zero-infra default."""

    return backend if backend is not None else backend_factory()


class GateAssertionError(AssertionError):
    """Raised when a reachable, complete gate evaluation violates its contract."""


def _gating_failures(res: dict) -> list[str]:
    return [
        _label(c)
        for c in res["checks"]
        if not c["passed"] and not c["optional"] and not c["pending"]
    ]


def assert_gate(
    spec: dict,
    *,
    backend: Backend | None = None,
    backend_factory: BackendFactory = MemoryBackend,
    ontology=None,
    policy: GatePolicy | None = None,
    strict_infra: bool = False,
) -> dict:
    """Evaluate ``spec`` and raise ``GateAssertionError`` on a contract violation.

    ``backend`` defaults to a fresh zero-infra memory backend. ``backend_factory`` is the
    explicit extension seam for choosing another default without consulting a process-wide
    registry or ambient settings. ``strict_infra`` makes an unreachable store fail too
    (default False returns the inconclusive result without raising).
    """
    backend = _resolve_backend(backend, backend_factory)
    res = evaluate(backend, spec, ontology=ontology, policy=policy)
    if not res["reachable"] or not res.get("complete", True):
        # Store unreachable OR a truncated/incomplete read: inconclusive evidence (?), not a
        # falsification (⊥). Skip unless the caller opted into ``strict_infra`` — an infra
        # An outage or undercounted read is not a contract violation. Treating a
        # truncated read as one would turn unavailable evidence into a false result.
        if strict_infra:
            raise GateAssertionError(
                f"store unreachable or read incomplete for cid={res['cid']} (inconclusive)"
            )
        return res
    if not res["ok"]:
        raise GateAssertionError(
            f"event contract violated (cid={res['cid']}): {_gating_failures(res)}"
        )
    return res


def explain(result: dict) -> str:
    """Return a one-line summary with stable check kinds for failed contracts."""
    if not result.get("reachable", True) or not result.get("complete", True):
        return f"INCONCLUSIVE (store unreachable/incomplete) cid={result.get('cid')}"
    if result.get("ok"):
        return f"SATISFIED cid={result.get('cid')}"
    parts = [f"{c.get('kind', '?')}:{_label(c)}" for c in failed_checks(result)]
    return f"VIOLATED cid={result.get('cid')}: " + ", ".join(parts)


def assert_present(
    cid: str,
    *matchers: dict,
    backend: Backend | None = None,
    backend_factory: BackendFactory = MemoryBackend,
    policy: GatePolicy | None = None,
    strict_infra: bool = False,
) -> dict:
    """Assert each matcher matched at least one event, in any order."""
    spec = {"cid": cid, "expect": [{"present": list(matchers)}]}
    return assert_gate(
        spec,
        backend=backend,
        backend_factory=backend_factory,
        policy=policy,
        strict_infra=strict_infra,
    )
