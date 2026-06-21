"""The `metamorphic:` check — the oracle-FREE escape (PROM08 / Chen et al. 1998).

Assert a RELATION between two reductions over two matched event sets that holds iff the computation
is correct, needing NO absolute oracle: idempotency (run twice -> same total), scaling (2x in ->
2x out), subset, monotone. It catches a class of wrong-but-strong behavior with no external truth —
but both sides are still the system's own emit, so a fault COMMON to both runs is invisible (it
raises difficulty, not grounding; within-run metamorphic stays derived-self / single_authority).
"""
from ooptdd.engine.gate import evaluate_events


def _ev(name, ts=1, **f):
    return {"event": name, "_timestamp": ts, **f}


def _eval(rule, events):
    return evaluate_events({"expect": [rule]}, events, reachable=True, complete=True, cid="c")


def _mm(relation, a, b, **kw):
    return {"metamorphic": {"relation": relation, "a": a, "b": b, **kw}}


def test_idempotent_double_charge_caught_with_no_oracle():
    rule = _mm("idempotent", {"event": "charge", "where": {"attempt": 1}},
               {"event": "charge", "where": {"attempt": 2}}, reduce="sum", field="amount")
    good = _eval(rule, [_ev("charge", 1, attempt=1, amount=42),
                        _ev("charge", 2, attempt=2, amount=42)])
    assert good["ok"] is True  # a correct retry re-charges the SAME total
    bug = _eval(rule, [_ev("charge", 1, attempt=1, amount=42),
                       _ev("charge", 2, attempt=2, amount=42),
                       _ev("charge", 3, attempt=2, amount=42)])
    assert bug["ok"] is False  # double-charge: attempt2 sum 84 != attempt1 42 -> RED, NO oracle


def test_metamorphic_no_evidence_when_a_side_is_empty():
    rule = _mm("equal", {"event": "a"}, {"event": "b"}, reduce="sum", field="x")
    res = _eval(rule, [_ev("a", 1, x=5)])
    assert res["ok"] is False and res["checks"][0]["reason"] == "metamorphic_no_evidence"


def test_scaled_relation():
    rule = _mm("scaled", {"event": "in"}, {"event": "out"}, reduce="sum", field="v", factor=2.0)
    assert _eval(rule, [_ev("in", 1, v=10), _ev("out", 2, v=20)])["ok"] is True   # 20 == 2*10
    assert _eval(rule, [_ev("in", 1, v=10), _ev("out", 2, v=19)])["ok"] is False  # off by one


def test_subset_and_monotone_operator_mapping():
    sub = _mm("subset", {"event": "a"}, {"event": "b"}, reduce="count")
    assert _eval(sub, [_ev("a"), _ev("a"), _ev("b")])["ok"] is True   # b(1) <= a(2)
    assert _eval(sub, [_ev("a"), _ev("b"), _ev("b")])["ok"] is False  # b(2) <= a(1) false
    mon = _mm("monotone", {"event": "a"}, {"event": "b"}, reduce="count")
    assert _eval(mon, [_ev("a"), _ev("b"), _ev("b")])["ok"] is True   # b(2) >= a(1)


def test_metamorphic_strength_high_but_grounding_still_self():
    res = _eval(_mm("equal", {"event": "a"}, {"event": "b"}, reduce="sum", field="x"),
                [_ev("a", 1, x=5), _ev("b", 2, x=5)])
    assert res["ok"] is True
    assert res["scope"]["by_strength"] == {"metamorphic": 1}
    # within-run metamorphic is still the system's own emit -> NOT corroboration
    assert res["oracle"]["single_authority"] is True
