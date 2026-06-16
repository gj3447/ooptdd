"""Tier-2 #7 (pending pacts + can_i_deploy) and #9 (weight/threshold, trajectory, assert_gate)."""
from __future__ import annotations

import pytest

from ooptdd import assert_gate, assert_present, can_i_deploy
from ooptdd.assertions import TraceAssertionError
from ooptdd.backends.base import QueryResult
from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.gate import evaluate


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


def _ship(backend, cid, *events):
    backend.ship([{"cid": cid, **e} for e in events])


# ── #7 pending: verified, surfaced, but does NOT gate ─────────────────────────
def test_pending_miss_does_not_gate_but_is_surfaced():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "wired"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"event": "wired", "op": ">=", "count": 1},
        {"event": "not_yet_wired", "op": ">=", "count": 1, "pending": True},
    ]})
    assert res["ok"] is True
    assert res["pending_failed"] == ["not_yet_wired"]
    assert res["pending_satisfied"] == []


def test_pending_satisfied_signals_promotion():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "now_wired"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"event": "now_wired", "op": ">=", "count": 1, "pending": True},
    ]})
    assert res["ok"] is True and res["pending_satisfied"] == ["now_wired"]


def test_required_miss_still_reds_even_with_pending_present():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "x"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"event": "required", "op": ">=", "count": 1},
        {"event": "later", "op": ">=", "count": 1, "pending": True},
    ]})
    assert res["ok"] is False


# ── #7 can_i_deploy across gates ──────────────────────────────────────────────
def test_can_i_deploy_blocks_on_real_red():
    green = {"cid": "a", "reachable": True, "ok": True, "pending_failed": []}
    red = {"cid": "b", "reachable": True, "ok": False, "pending_failed": []}
    d = can_i_deploy([green, red])
    assert d["deployable"] is False and d["blockers"] == ["b"]


def test_can_i_deploy_holds_on_inconclusive():
    infra = {"cid": "a", "reachable": False, "ok": False, "pending_failed": []}
    d = can_i_deploy([infra])
    assert d["deployable"] is False and d["inconclusive"] == ["a"]


def test_can_i_deploy_allows_when_only_pending_owed():
    g = {"cid": "a", "reachable": True, "ok": True, "pending_failed": ["owed_evt"]}
    d = can_i_deploy([g])
    assert d["deployable"] is True and d["pending"] == {"a": ["owed_evt"]}


# ── #9 weighted threshold (promptfoo test-level threshold) ────────────────────
def test_weighted_threshold_passes_on_quorum():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"}, {"event": "b"})  # c missing
    res = evaluate(b, {"cid": "c1", "threshold": 0.6, "expect": [
        {"event": "a", "weight": 1},
        {"event": "b", "weight": 1},
        {"event": "c", "weight": 1},
    ]})
    assert res["ok"] is True and abs(res["score"] - 2 / 3) < 1e-9


def test_weighted_threshold_fails_below_quorum():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"})  # only 1 of 3
    res = evaluate(b, {"cid": "c1", "threshold": 0.6, "expect": [
        {"event": "a"}, {"event": "b"}, {"event": "c"},
    ]})
    assert res["ok"] is False and res["score"] < 0.6


def test_weight_dominates_score():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "heavy"})  # heavy passes, light misses
    res = evaluate(b, {"cid": "c1", "threshold": 0.75, "expect": [
        {"event": "heavy", "weight": 9},
        {"event": "light", "weight": 1},
    ]})
    assert res["ok"] is True and res["score"] == 0.9


# ── #9 trajectory alias (ordered tool/event sequence) ─────────────────────────
class _Fixed:
    default_lookback_s = 3600
    default_future_buffer_s = 0

    def __init__(self, events):
        self._events = events

    def ship(self, events):  # pragma: no cover
        pass

    def query(self, cid, *, since_us, until_us):
        return QueryResult(reachable=True, events=list(self._events))


def test_trajectory_is_must_order_alias():
    b = _Fixed([{"event": "plan", "_timestamp": 1}, {"event": "act", "_timestamp": 2}])
    res = evaluate(b, {"cid": "c1", "expect": [{"trajectory": ["plan", "act"]}]})
    assert res["ok"] and res["checks"][0]["must_order"] == ["plan", "act"]


def test_trajectory_out_of_order_reds():
    b = _Fixed([{"event": "plan", "_timestamp": 2}, {"event": "act", "_timestamp": 1}])
    res = evaluate(b, {"cid": "c1", "expect": [{"trajectory": ["plan", "act"]}]})
    assert not res["ok"]


# ── #9 assert_gate / assert_present (DeepEval-style in-test assertion) ─────────
def test_assert_present_passes_on_memory():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "execute_tool"})
    assert_present("c1", {"event": "execute_tool"}, backend=b)  # no raise


def test_assert_gate_raises_on_red():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"})
    with pytest.raises(TraceAssertionError):
        assert_gate({"cid": "c1", "expect": [{"event": "missing"}]}, backend=b)


def test_assert_gate_skips_inconclusive_by_default():
    class _Unreachable:
        default_lookback_s = 3600
        default_future_buffer_s = 0

        def ship(self, events):  # pragma: no cover
            pass

        def query(self, cid, *, since_us, until_us):
            return QueryResult(reachable=False, events=[])

    # default: inconclusive does not raise
    res = assert_gate({"cid": "c1", "expect": [{"event": "a"}]}, backend=_Unreachable())
    assert res["reachable"] is False
    # strict_infra: it does
    with pytest.raises(TraceAssertionError):
        assert_gate({"cid": "c1", "expect": [{"event": "a"}]},
                    backend=_Unreachable(), strict_infra=True)
