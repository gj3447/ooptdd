"""The check-predicate registry seam (gate.py) — absorbed from pluggy/hypothesis.

New predicates register via ``@check()`` WITHOUT editing ``evaluate()``, dispatch is a
registry lookup (not an if-elif), and the registry is a structural-assertion surface:
every probed keyword resolves to a registered handler (no orphan branch).
"""
from __future__ import annotations

import pytest

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.gate import _KEY_PROBES, CHECK_REGISTRY, _detect_check_key, check, evaluate


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


def _ship(backend, cid, *events):
    backend.ship([{"cid": cid, **e} for e in events])


def test_registry_has_all_builtin_predicates():
    for key in ("absent", "heartbeat", "must_order", "present", "ratioMetric", "conforms"):
        assert key in CHECK_REGISTRY and callable(CHECK_REGISTRY[key])


def test_no_orphan_dispatch_every_probe_resolves():
    # the registry analogue of "no elif branch without a body": every spec keyword the
    # dispatcher probes (incl. the forbid/trajectory synonyms) maps to a real handler.
    for _spec_key, canon in _KEY_PROBES:
        assert canon in CHECK_REGISTRY


def test_detect_check_key_preserves_historical_precedence():
    # The seam's equivalence to the old if-elif rests on an ordered structure
    # (_KEY_PROBES + CHECK_REGISTRY insertion order). Pin the relative precedence for
    # degenerate multi-keyword rules so a future reorder of decorators/_KEY_PROBES is
    # caught (the old ladder was: absent/forbid > heartbeat > must_order/trajectory >
    # present > ratioMetric > conforms > default-count).
    assert _detect_check_key({"absent": 1, "present": 1}) == "absent"
    assert _detect_check_key({"forbid": 1, "present": 1}) == "absent"
    assert _detect_check_key({"heartbeat": 1, "present": 1}) == "heartbeat"
    assert _detect_check_key({"must_order": 1, "ratioMetric": 1}) == "must_order"
    assert _detect_check_key({"trajectory": 1, "present": 1}) == "must_order"
    assert _detect_check_key({"present": 1, "ratioMetric": 1}) == "present"
    assert _detect_check_key({"ratioMetric": 1, "conforms": 1}) == "ratioMetric"
    # a plain count / indicatorRef rule carries no predicate key -> default count check
    assert _detect_check_key({"event": "x", "op": ">=", "count": 1}) is None
    assert _detect_check_key({"indicatorRef": "r", "op": "eq", "target": 0}) is None


def test_duplicate_registration_raises():
    with pytest.raises(ValueError):
        @check("present")  # already registered — silent-overwrite guard must fire
        def _dup(events, rule, ctx):  # pragma: no cover
            return {}


def test_custom_predicate_registers_without_editing_gate_py():
    # THE point of the seam: extend the gate vocabulary from OUTSIDE gate.py.
    @check("spike")
    def _spike(events, rule, ctx):
        n = sum(1 for e in events if e.get("event") == rule["spike"])
        return {"spike": rule["spike"], "got": n, "passed": ctx.reachable and n >= 1}

    try:
        b = MemoryBackend()
        _ship(b, "c1", {"event": "boom"})
        res = evaluate(b, {"cid": "c1", "expect": [{"spike": "boom"}]})
        assert res["ok"]
        assert any(c.get("spike") == "boom" for c in res["checks"])
        # and it genuinely gates — a missing spike is RED
        b2 = MemoryBackend()
        _ship(b2, "c2", {"event": "quiet"})
        res2 = evaluate(b2, {"cid": "c2", "expect": [{"spike": "boom"}]})
        assert not res2["ok"]
    finally:
        CHECK_REGISTRY.pop("spike", None)


def test_forbid_error_injection_fires_exactly_once(monkeypatch):
    # env-forbid injection stays a single pre-loop append (the selector_gates "N->once"
    # invariant) and routes through the registry's `absent` handler.
    monkeypatch.setenv("OOPTDD_FORBID_ERRORS", "1")
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"}, {"event": "b"}, {"event": "boom", "level": "ERROR"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"present": [{"event": "a"}]},
        {"present": [{"event": "b"}]},
    ]})
    absent_checks = [c for c in res["checks"] if "absent" in c]
    assert len(absent_checks) == 1  # injected once, not per-rule
    assert not res["ok"]  # the ERROR record flips the gate
