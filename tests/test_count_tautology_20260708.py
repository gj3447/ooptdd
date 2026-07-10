"""A failure-incapable check must not license a green (audit 2026-07-08, #2).

`count >= 0` (and any `>= n` with n<=0) is satisfied by any prefix, the empty one included —
it can never fail. Yet it is a gating check (not optional/pending), so it made
`asserts_anything` True and let a gate whose ONLY check is `count >= 0` read GREEN while
proving nothing (the vacuous guard was bypassed). The CountMonitor now flags such a check
`tautological`, the gate excludes tautological checks from `gating` (so the gate is vacuous,
not green), and `lint_spec` raises the new blocking VAC4 at author time.
"""
from __future__ import annotations

from ooptdd.engine.gate import evaluate_events, lint_spec
from ooptdd.engine.monitor import CountMonitor


def _ev(name, ts=1, **f):
    return {"event": name, "_timestamp": ts, **f}


def _eval(expect, events):
    return evaluate_events({"expect": expect}, events, reachable=True, complete=True, cid="c")


def _codes(spec):
    return {f["code"] for f in lint_spec(spec)}


def test_count_ge_zero_tautology_is_vacuous_not_green():
    # a gate whose only check is count>=0 asserts nothing that can fail
    res = _eval([{"event": "x", "op": ">=", "count": 0}], [])
    assert res["ok"] is False and res["vacuous"] is True and res["scope"]["gating"] == 0
    # even with a matching event it stays vacuous (the check still asserts nothing)
    res2 = _eval([{"event": "x", "op": ">=", "count": 0}], [_ev("x")])
    assert res2["ok"] is False and res2["vacuous"] is True


def test_count_ge_zero_is_marked_tautological():
    assert CountMonitor("a", {}, ">=", 0).collapse(True)["tautological"] is True
    # honest checks are NOT tautological (they can fail)
    assert CountMonitor("a", {}, ">=", 1).collapse(True)["tautological"] is False
    assert CountMonitor("a", {}, "==", 0).collapse(True)["tautological"] is False
    assert CountMonitor("a", {}, "<=", 0).collapse(True)["tautological"] is False


def test_lint_vac4_count_ge_zero_tautology():
    codes = _codes({"expect": [{"event": "x", "op": ">=", "count": 0}]})
    assert "VAC4" in codes
    assert any(f["code"] == "VAC4" and f["severity"] == "high"
               for f in lint_spec({"expect": [{"event": "x", "op": ">=", "count": 0}]}))
    # a normal existence check (count>=1) can fail if x is absent -> NOT VAC4 (stays VAC3)
    c1 = _codes({"expect": [{"event": "x", "op": ">=", "count": 1}]})
    assert "VAC4" not in c1 and "VAC3" in c1


def test_honest_count_ge_one_still_gates_and_greens():
    # over-reach guard: a real count>=1 check must remain gating and pass when satisfied
    res = _eval([{"event": "x", "op": ">=", "count": 1}], [_ev("x")])
    assert res["ok"] is True and res["vacuous"] is False and res["scope"]["gating"] == 1


def test_honest_equals_zero_stays_gating():
    # `== 0` (expect-zero: e.g. zero NG events) CAN fail (a match breaks it) -> not tautological,
    # stays a real gating check.
    res_ok = _eval([{"event": "ng", "op": "==", "count": 0}], [])
    assert res_ok["ok"] is True and res_ok["scope"]["gating"] == 1
    res_bad = _eval([{"event": "ng", "op": "==", "count": 0}], [_ev("ng")])
    assert res_bad["ok"] is False
