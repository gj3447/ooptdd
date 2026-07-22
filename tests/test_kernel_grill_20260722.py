"""Kernel LTL3 grill regressions (2026-07-22 adversarial review of monitor.py/verify.py).

These were PRE-EXISTING kernel holes (not introduced this session) surfaced by the
grill and fixed together:
  F2  allow_errors fail-open (empty entry disables the wing; allowlist bleeds into
      user absent checks)
  F3  HeartbeatMonitor blind to leading/trailing silence
  F4  tautology bypass: `> -1` / `!= -1` read GREEN with no gating/lint flag
(F1 must_order early-settle lives in test_verify_generic.py.)
"""
import pytest

from ooptdd.engine.gate import evaluate_events, lint_spec

CID = "kernel-cid"


def _ev(event, ts, **kw):
    return {"event": event, "cid": CID, "_timestamp": ts, **kw}


def _res(expect, events, **spec):
    return evaluate_events({"cid": CID, "expect": expect, **spec}, events,
                           reachable=True, cid=CID)


# ── F2: allow_errors fail-open ─────────────────────────────────────────────────


def test_f2a_empty_allow_entry_is_a_loud_error_not_a_disabled_wing():
    with pytest.raises(ValueError, match="matches every event"):
        _res([{"event": "boom", "op": "gte", "target": 1}],
             [_ev("boom", 1, level="ERROR")],
             forbid_errors=True, allow_errors=[{}])


def test_f2b_allowlist_does_not_bleed_into_a_user_absent_check():
    """The spec-level allow_errors (for the injected error wing) must NOT exempt a
    user-authored `absent:` — the user forbidding zdf.drop@B means it."""
    res = _res([{"absent": [{"event": "zdf.drop", "where": {"station": "B"}}]}],
               [_ev("zdf.drop", 1, station="B")],
               allow_errors=[{"event": "zdf.drop"}])
    assert not res["ok"]  # the forbidden event is present; the allowlist must not save it


def test_f2_allowlist_still_exempts_the_error_wing():
    """The legitimate use still works: a benign error named in allow_errors does not RED
    the injected forbid_errors wing."""
    res = _res([{"event": "ok", "op": "gte", "target": 1}],
               [_ev("ok", 1), _ev("zdf.drop", 2, level="ERROR")],
               forbid_errors=True, allow_errors=[{"event": "zdf.drop"}])
    assert res["ok"]


# ── F3: heartbeat leading/trailing silence ─────────────────────────────────────


def test_f3_trailing_silence_reds_a_dead_heartbeat():
    """The service kept emitting `other` for 98s after the last beat — a dead heartbeat
    the inter-beat check alone was blind to."""
    res = _res([{"heartbeat": "hb", "every_s": 1}],
               [_ev("hb", 0), _ev("other", 98_000_000)])
    chk = res["checks"][0]
    assert not chk["passed"] and chk["reason"] == "trailing_silence"


def test_f3_leading_silence_reds():
    res = _res([{"heartbeat": "hb", "every_s": 1}],
               [_ev("other", 0), _ev("hb", 50_000_000)])
    assert not res["checks"][0]["passed"]
    assert res["checks"][0]["reason"] == "leading_silence"


def test_f3_healthy_heartbeat_still_green():
    res = _res([{"heartbeat": "hb", "every_s": 2}],
               [_ev("hb", 0), _ev("hb", 1_000_000), _ev("hb", 2_000_000)])
    assert res["checks"][0]["passed"]


# ── F4: tautology bypass ───────────────────────────────────────────────────────


@pytest.mark.parametrize("op,target", [(">", -1), ("ne", -1), (">=", 0)])
def test_f4_tautological_count_is_not_gating_and_is_linted(op, target):
    spec_expect = [{"event": "x", "op": op, "target": target}]
    res = _res(spec_expect, [])
    chk = res["checks"][0]
    assert chk["tautological"] is True
    # a gate whose ONLY check is a tautology asserts nothing gating -> not a clean pass
    assert not res["ok"] and res["vacuous"] is True
    codes = [f["code"] for f in lint_spec({"cid": CID, "expect": spec_expect})]
    assert "VAC4" in codes


def test_f4_legitimate_counts_still_gate():
    for op, target in [(">", 0), (">=", 1), ("ne", 1)]:
        res = _res([{"event": "x", "op": op, "target": target}], [_ev("x", 1)])
        assert res["checks"][0]["tautological"] is False
