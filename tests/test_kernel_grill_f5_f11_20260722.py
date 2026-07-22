"""Kernel grill regressions F5/F6/F9/F10/F11 (2026-07-22, round 2 — verify + monitor).

Pre-existing kernel holes surfaced by the LTL3 grill and fixed together:
  F5  verify_trace issued ⊥ absent when the LAST read was unreachable (stale evidence)
  F6  verify_trace settled a flaky ⊥ partial-loss when the summary was indexed before its
      outcomes (which arrive a poll later)
  F9  must_order/trajectory silently deduped repeated names ([a,a,b] passed on one a)
  F10 CountMonitor int()-truncated float targets (>=1.9 passed on 1) and crashed on strings
  F11 RatioMonitor accepted good>total (ratio>1 read GREEN under >=0.99)
"""
import pytest

from ooptdd.backends.base import QueryResult
from ooptdd.engine.gate import evaluate_events
from ooptdd.engine.verify import verify_trace


class _Clock:
    def now_us(self):
        return 1_000_000_000


def _nosleep(_):
    pass


def _chk(expect, events):
    return evaluate_events({"cid": "c", "expect": expect}, events,
                           reachable=True, cid="c")["checks"][0]


# ── F9: must_order duplicate names ─────────────────────────────────────────────


def test_f9_duplicate_order_names_are_a_loud_error():
    with pytest.raises(ValueError, match="distinct"):
        _chk([{"must_order": ["a", "a", "b"]}], [])
    with pytest.raises(ValueError, match="distinct"):
        _chk([{"trajectory": ["x", "x"]}], [])


def test_f9_distinct_order_names_still_work():
    r = _chk([{"must_order": ["a", "b"]}],
             [{"event": "a", "cid": "c", "_timestamp": 1},
              {"event": "b", "cid": "c", "_timestamp": 2}])
    assert r["passed"]


# ── F10: float / string count targets ──────────────────────────────────────────


def test_f10_float_target_not_truncated():
    r = _chk([{"event": "x", "op": ">=", "target": 1.9}],
             [{"event": "x", "cid": "c", "_timestamp": 1}])
    assert r["want"] == 1.9 and not r["passed"]  # 1 >= 1.9 is False, not truncated to >=1


def test_f10_numeric_string_target_parses_not_crashes():
    r = _chk([{"event": "x", "op": ">=", "target": "2"}],
             [{"event": "x", "cid": "c", "_timestamp": 1}])
    assert r["want"] == 2 and not r["passed"]
    r2 = _chk([{"event": "x", "op": ">=", "target": "1.5"}], [])
    assert r2["want"] == 1.5


def test_f10_non_numeric_target_is_a_clean_error():
    with pytest.raises(ValueError, match="numeric"):
        _chk([{"event": "x", "op": ">=", "target": "lots"}], [])


# ── F11: ratio good > total ────────────────────────────────────────────────────


def test_f11_good_exceeds_total_is_never_a_clean_pass():
    r = _chk([{"ratioMetric": {"good": {"event": "a"},
                               "total": {"event": "a", "where": {"k": "only1"}}},
               "op": "gte", "target": 0.99}],
             [{"event": "a", "cid": "c", "k": "only1", "_timestamp": 1},
              {"event": "a", "cid": "c", "k": "other", "_timestamp": 2}])
    assert not r["passed"] and r["reason"] == "ratio_good_exceeds_total"


def test_f11_normal_ratio_still_evaluates():
    r = _chk([{"ratioMetric": {"good": {"event": "a", "where": {"ok": True}},
                               "total": {"event": "a"}}, "op": "gte", "target": 0.5}],
             [{"event": "a", "cid": "c", "ok": True, "_timestamp": 1},
              {"event": "a", "cid": "c", "ok": False, "_timestamp": 2}])
    assert r["passed"] and r["value"] == 0.5


# ── F5/F6: verify_trace evidence honesty ───────────────────────────────────────


class _ScriptedBackend:
    """Returns a scripted QueryResult per attempt."""
    default_lookback_s = 3600
    default_future_buffer_s = 0
    queryable = True

    def __init__(self, results):
        self._results = results
        self.n = 0

    def ship(self, events):
        pass

    def query(self, cid, *, since_us, until_us):
        r = self._results[min(self.n, len(self._results) - 1)]
        self.n += 1
        return r


def _verify(results, **kw):
    return verify_trace(_ScriptedBackend(results), "c", clock=_Clock(),
                        sleeper=_nosleep, **kw)


def test_f5_unreachable_last_read_is_inconclusive_not_absent():
    """attempt 1 reachable+empty (premature), then the store goes down: the ⊥ absent must
    NOT be issued off the stale empty read — the last read has no evidence."""
    out = _verify([QueryResult(reachable=True, events=[]),
                   QueryResult(reachable=False)], retries=4)
    assert out["verdict"] == "inconclusive"


def test_f5_reachable_complete_empty_is_still_absent():
    """The genuine ⊥: the last read WAS reachable+complete and showed no summary."""
    out = _verify([QueryResult(reachable=True, events=[])], retries=2)
    assert out["verdict"] == "absent"


def test_f6_summary_before_outcomes_is_not_a_flaky_partial_red():
    """The summary is indexed before its outcomes (they arrive a poll later) — must keep
    polling and settle GREEN, not RED at attempt 1."""
    session = {"event": "test_session", "total": 3, "_timestamp": 5}
    outcomes = [{"event": "test_outcome", "_timestamp": 6 + i} for i in range(3)]
    out = _verify([QueryResult(reachable=True, events=[session]),
                   QueryResult(reachable=True, events=[session, *outcomes])], retries=4)
    assert out["verdict"] == "present" and out["ok"] and out["attempts"] == 2


def test_f6_real_partial_loss_still_reds_on_the_final_poll():
    """A summary whose outcomes never complete IS partial loss — the final poll reds it."""
    session = {"event": "test_session", "total": 3, "_timestamp": 5}
    out = _verify([QueryResult(reachable=True,
                               events=[session, {"event": "test_outcome", "_timestamp": 6}])],
                  retries=3)
    assert out["verdict"] == "present" and not out["ok"]
    assert any("partial_loss" in r for r in out["reasons"])


def test_f6_forged_partial_summary_gets_no_polling_grace():
    """Pre-commit verification caught this: `total` is attacker-controlled, so a forged
    summary inflated to look partial must NOT be given keep-polling grace — a definitive
    sig_invalid settles RED at attempt 1, so a later store flap can't downgrade it to
    inconclusive/absent."""
    forged = {"event": "test_session", "total": 5, "sig": "deadbeef", "_timestamp": 5,
              "service": "x", "passed": 1, "failed": 0, "skipped": 0}
    one_outcome = {"event": "test_outcome", "_timestamp": 6}
    # forged+partial at poll 1, then the store flaps unreachable — must still catch the forgery
    out = _verify([QueryResult(reachable=True, events=[forged, one_outcome]),
                   QueryResult(reachable=False)], retries=4, signing_key="k")
    assert out["verdict"] == "present" and not out["ok"]
    assert any("sig_invalid" in r for r in out["reasons"]), out
    assert out["attempts"] == 1  # settled immediately, no grace


def test_f6_expect_total_mismatch_is_definitive_no_grace():
    """An expect_total mismatch is definitive too — settle now, don't keep polling."""
    session = {"event": "test_session", "total": 2, "_timestamp": 5}
    out = _verify([QueryResult(reachable=True, events=[session]),
                   QueryResult(reachable=False)], retries=4, expect_total=9)
    assert out["verdict"] == "present" and not out["ok"]
    assert any("!=expect9" in r for r in out["reasons"])


def test_f10_non_finite_target_rejected():
    with pytest.raises(ValueError, match="finite"):
        _chk([{"event": "x", "op": ">=", "target": float("inf")}], [])
