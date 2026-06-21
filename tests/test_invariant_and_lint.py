"""Two real-fixes from the PROM blind-spot study (cycle-prom07):

- ``invariant`` — a cross-event conservation primitive. Makes a *relation between events*
  expressible (sum/count/min/max/last per side, compared with a tolerance), so a value-
  CONSISTENCY bug becomes catchable — including the emit-without-effect green (a payment event
  with no amount). Honesty boundary: intra-trace, single-authority — it catches inconsistency
  between emitted events, not emit-vs-truth.
- ``lint_spec`` — a static, offline strength audit that flags a vacuously-satisfiable gate
  BEFORE any events (the pseudo-tested-gate detector).
"""
from ooptdd.engine.gate import evaluate_events, lint_spec


def _ev(name, ts=1, **f):
    return {"event": name, "_timestamp": ts, **f}


def _eval(expect, events):
    return evaluate_events({"expect": expect}, events, reachable=True, complete=True, cid="c")


def _inv(left, right, op="==", tol=0.0):
    return {"invariant": {"left": left, "right": right, "op": op, "tol": tol}}


# ── invariant (conservation) ──────────────────────────────────────────────────
def test_invariant_conservation_balances_is_green_and_high_strength():
    res = _eval(
        [_inv({"reduce": "sum", "field": "amount", "event": "pay"},
              {"reduce": "sum", "field": "amount", "event": "ship"})],
        [_ev("pay", 1, amount=40), _ev("pay", 2, amount=2), _ev("ship", 3, amount=42)],
    )
    assert res["ok"] is True
    assert res["scope"]["by_strength"] == {"invariant": 1}  # not existence-only — real strength


def test_invariant_imbalance_is_red():
    res = _eval(
        [_inv({"reduce": "sum", "field": "amount", "event": "pay"},
              {"reduce": "sum", "field": "amount", "event": "ship"})],
        [_ev("pay", 1, amount=42), _ev("ship", 2, amount=40)],
    )
    assert res["ok"] is False


def test_invariant_tolerance():
    evs = [_ev("x", 1, a=42.0), _ev("y", 2, a=42.4)]
    sides = ({"reduce": "sum", "field": "a", "event": "x"},
             {"reduce": "sum", "field": "a", "event": "y"})
    assert _eval([_inv(*sides, tol=0.1)], evs)["ok"] is False   # 0.4 > 0.1
    assert _eval([_inv(*sides, tol=0.5)], evs)["ok"] is True    # 0.4 <= 0.5


def test_invariant_no_evidence_kills_emit_without_effect():
    # `pay` emitted with NO amount -> left has no numeric value -> nothing to relate -> RED.
    # This is the moment the emit-without-effect green dies: assert a VALUE relation and an event
    # that names an effect it never carried can no longer pass.
    res = _eval(
        [_inv({"reduce": "sum", "field": "amount", "event": "pay"},
              {"reduce": "sum", "field": "amount", "event": "ship"})],
        [_ev("pay", 1), _ev("ship", 2, amount=42)],
    )
    assert res["ok"] is False
    assert res["checks"][0]["reason"] == "invariant_no_evidence"


def test_invariant_count_conservation_request_response():
    bal = [_inv({"reduce": "count", "event": "req"}, {"reduce": "count", "event": "resp"})]
    assert _eval(bal, [_ev("req", 1), _ev("req", 2), _ev("resp", 3), _ev("resp", 4)])["ok"] is True
    assert _eval(bal, [_ev("req", 1), _ev("req", 2), _ev("resp", 3)])["ok"] is False  # 2 != 1


def test_invariant_inequality_op():
    res = _eval([_inv({"reduce": "count", "event": "ok"}, {"reduce": "count", "event": "err"},
                      op=">")],
                [_ev("ok", 1), _ev("ok", 2), _ev("err", 3)])
    assert res["ok"] is True  # 2 > 1


# ── lint_spec (static anti-vacuity) ───────────────────────────────────────────
def _codes(spec):
    return {f["code"] for f in lint_spec(spec)}


def test_lint_vac0_empty_expect():
    assert _codes({"expect": []}) == {"VAC0"}


def test_lint_vac1_all_optional_or_pending():
    assert "VAC1" in _codes({"expect": [{"event": "a", "optional": True},
                                        {"event": "b", "pending": True}]})


def test_lint_vac2_threshold_without_justification():
    assert "VAC2" in _codes({"expect": [{"event": "a"}], "threshold": 0.5})
    # a justification field suppresses VAC2 (intentional quorum)
    assert "VAC2" not in _codes({"expect": [{"event": "a"}], "threshold": 0.5,
                                 "justification": "concurrent region, set quorum by design"})


def test_lint_vac3_existence_only_check():
    assert "VAC3" in _codes({"expect": [{"event": "a"}]})  # no where/order/forbid


def test_lint_clean_strong_spec_has_no_blocking_findings():
    f = lint_spec({"expect": [{"event": "a", "where": {"k": "v"}}, {"must_order": ["a", "b"]}]})
    assert not any(x["severity"] == "high" for x in f)  # value-pinned + ordered, non-vacuous
