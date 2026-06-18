"""In-test assertion helpers (Tier-2 #9) — DeepEval ``assert_test`` ergonomics.

Use ooptdd directly inside an ordinary pytest test: write a gate spec (or a list of
expectations), evaluate it against a backend, and raise on RED — so a *trace
expectation* becomes a first-class pytest assertion, the way DeepEval's
``assert_test(case, metrics)`` turns an LLM metric into a unit test.

This is distinct from the session-level plugin, which never fails the build
("observation does not override the verdict"). These are assertions you opted into
explicitly: a real, reachable miss (RED) raises ``TraceAssertionError``; an
unreachable store (``inconclusive``) is skipped unless ``strict_infra=True`` —
keeping the rule that an infra outage is not a falsification.
"""
from __future__ import annotations

from .backends import Backend, get_backend
from .engine.gate import _label, evaluate


class TraceAssertionError(AssertionError):
    """Raised when a gate evaluates RED — a real miss against a reachable store."""


def _gating_failures(res: dict) -> list[str]:
    return [_label(c) for c in res["checks"]
            if not c["passed"] and not c["optional"] and not c["pending"]]


def assert_gate(spec: dict, *, backend: Backend | None = None, ontology=None,
                strict_infra: bool = False) -> dict:
    """Evaluate ``spec`` and raise ``TraceAssertionError`` on RED. Returns the result dict.

    ``backend`` defaults to the zero-infra memory backend. ``strict_infra`` makes an
    unreachable store fail too (default False → inconclusive is skipped).
    """
    backend = backend or get_backend("memory")
    res = evaluate(backend, spec, ontology=ontology)
    if not res["reachable"]:
        if strict_infra:
            raise TraceAssertionError(f"store unreachable for cid={res['cid']} (inconclusive)")
        return res
    if not res["ok"]:
        raise TraceAssertionError(
            f"trace gate RED (cid={res['cid']}): {_gating_failures(res)}"
        )
    return res


def assert_present(cid: str, *matchers: dict, backend: Backend | None = None,
                   strict_infra: bool = False) -> dict:
    """Assert each matcher (``{"event":..., "where":...}``) matched ≥1 event, any order.

    Shorthand for a single ``present`` gate — the everyday "did these events happen?"
    check, DeepEval-style: ``assert_present(cid, {"event": "execute_tool"})``.
    """
    spec = {"cid": cid, "expect": [{"present": list(matchers)}]}
    return assert_gate(spec, backend=backend, strict_infra=strict_infra)
