"""Tier-2 pending results, aggregation, weighted thresholds, and assertions."""

from __future__ import annotations

import pytest

from ooptdd.assertions import GateAssertionError, assert_gate, assert_present
from ooptdd.backends.base import QueryResult
from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.bootstrap import compose_runtime
from ooptdd.engine.gate import combine_results, evaluate
from ooptdd.engine.gate_values import GatePolicy


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
    res = evaluate(
        b,
        {
            "cid": "c1",
            "expect": [
                {"event": "wired", "op": ">=", "count": 1},
                {"event": "not_yet_wired", "op": ">=", "count": 1, "pending": True},
            ],
        },
    )
    assert res["ok"] is True
    assert res["pending_failed"] == ["not_yet_wired"]
    assert res["pending_satisfied"] == []


def test_pending_satisfied_signals_promotion():
    # a real gating check alongside a now-passing pending one: the gate is non-vacuous (GREEN)
    # and pending_satisfied surfaces the promotion hint. (An all-pending gate asserts nothing
    # gating and is vacuous -> see test_gate_scope.test_all_pending_on_empty_store_is_vacuous.)
    b = MemoryBackend()
    _ship(b, "c1", {"event": "wired"})
    _ship(b, "c1", {"event": "now_wired"})
    res = evaluate(
        b,
        {
            "cid": "c1",
            "expect": [
                {"event": "wired", "op": ">=", "count": 1},
                {"event": "now_wired", "op": ">=", "count": 1, "pending": True},
            ],
        },
    )
    assert res["ok"] is True and res["pending_satisfied"] == ["now_wired"]


def test_required_miss_still_reds_even_with_pending_present():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "x"})
    res = evaluate(
        b,
        {
            "cid": "c1",
            "expect": [
                {"event": "required", "op": ">=", "count": 1},
                {"event": "later", "op": ">=", "count": 1, "pending": True},
            ],
        },
    )
    assert res["ok"] is False


# ── #7 neutral aggregation across independent results ────────────────────────
def test_combine_results_blocks_on_real_failure():
    green = {"cid": "a", "reachable": True, "ok": True, "pending_failed": []}
    red = {"cid": "b", "reachable": True, "ok": False, "pending_failed": []}
    d = combine_results([green, red])
    assert d["ok"] is False and d["failed"] == ["b"]


def test_combine_results_holds_on_inconclusive():
    infra = {"cid": "a", "reachable": False, "ok": False, "pending_failed": []}
    d = combine_results([infra])
    assert d["ok"] is False and d["inconclusive"] == ["a"]


def test_combine_results_allows_when_only_pending_owed():
    g = {"cid": "a", "reachable": True, "ok": True, "pending_failed": ["owed_evt"]}
    d = combine_results([g])
    assert d["ok"] is True and d["pending"] == {"a": ["owed_evt"]}


# ── #9 weighted threshold (promptfoo test-level threshold) ────────────────────
def test_weighted_threshold_passes_on_quorum():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"}, {"event": "b"})  # c missing
    res = evaluate(
        b,
        {
            "cid": "c1",
            "threshold": 0.6,
            "expect": [
                {"event": "a", "weight": 1},
                {"event": "b", "weight": 1},
                {"event": "c", "weight": 1},
            ],
        },
    )
    assert res["ok"] is True and abs(res["score"] - 2 / 3) < 1e-9


def test_weighted_threshold_fails_below_quorum():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"})  # only 1 of 3
    res = evaluate(
        b,
        {
            "cid": "c1",
            "threshold": 0.6,
            "expect": [
                {"event": "a"},
                {"event": "b"},
                {"event": "c"},
            ],
        },
    )
    assert res["ok"] is False and res["score"] < 0.6


def test_weight_dominates_score():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "heavy"})  # heavy passes, light misses
    res = evaluate(
        b,
        {
            "cid": "c1",
            "threshold": 0.75,
            "expect": [
                {"event": "heavy", "weight": 9},
                {"event": "light", "weight": 1},
            ],
        },
    )
    assert res["ok"] is True and res["score"] == 0.9


@pytest.mark.parametrize("weight", [-1, float("nan"), float("inf"), True])
def test_gate_rejects_unsafe_check_weights(weight):
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"})
    with pytest.raises(ValueError, match="gate check weight"):
        evaluate(b, {"cid": "c1", "expect": [{"event": "a", "weight": weight}]})


def test_weighted_gate_rejects_zero_total_weight():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"})
    with pytest.raises(ValueError, match="positive total gating weight"):
        evaluate(
            b,
            {
                "cid": "c1",
                "threshold": 0.5,
                "expect": [
                    {"event": "a", "weight": 0},
                    {"event": "b", "weight": 0},
                ],
            },
        )


@pytest.mark.parametrize("threshold", [0, -0.01, 1.01, float("nan"), float("inf"), True])
def test_weighted_gate_rejects_threshold_outside_positive_unit_interval(threshold):
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"})
    with pytest.raises(ValueError, match="gate threshold"):
        evaluate(
            b,
            {"cid": "c1", "threshold": threshold, "expect": [{"event": "a"}]},
        )


def test_all_failed_weighted_checks_cannot_become_green_at_zero_threshold():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"}, {"event": "a"})
    with pytest.raises(ValueError, match="threshold must be > 0"):
        evaluate(
            b,
            {
                "cid": "c1",
                "threshold": 0,
                "expect": [{"event": "a", "op": "==", "count": 1}],
            },
        )


# ── #9 trajectory alias (ordered tool/event sequence) ─────────────────────────
def _trajectory_provider():
    from ooptdd.engine.gate import core_check_registry

    ordered = core_check_registry()["must_order"]

    def trajectory(events, rule, ctx):
        converted = {**rule, "must_order": rule["trajectory"]}
        del converted["trajectory"]
        return ordered(events, converted, ctx)

    return {"trajectory": trajectory}


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
    runtime = compose_runtime(
        project={"extensions": ["trajectory"]},
        environment={},
        extension_providers={"trajectory": _trajectory_provider},
    )
    runtime = runtime.activate_extensions()
    res = runtime.evaluate(b, {"cid": "c1", "expect": [{"trajectory": ["plan", "act"]}]})
    assert res["ok"] and res["checks"][0]["must_order"] == ["plan", "act"]


def test_trajectory_out_of_order_reds():
    b = _Fixed([{"event": "plan", "_timestamp": 2}, {"event": "act", "_timestamp": 1}])
    runtime = compose_runtime(
        project={"extensions": ["trajectory"]},
        environment={},
        extension_providers={"trajectory": _trajectory_provider},
    )
    runtime = runtime.activate_extensions()
    res = runtime.evaluate(b, {"cid": "c1", "expect": [{"trajectory": ["plan", "act"]}]})
    assert not res["ok"]


# ── #9 assert_gate / assert_present (DeepEval-style in-test assertion) ─────────
def test_assert_present_passes_on_memory():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "execute_tool"})
    assert_present("c1", {"event": "execute_tool"}, backend=b)  # no raise


def test_assert_gate_default_backend_factory_is_injectable():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"})
    calls = []

    def factory():
        calls.append(True)
        return b

    res = assert_gate(
        {"cid": "c1", "expect": [{"event": "a"}]},
        backend_factory=factory,
    )

    assert res["ok"] is True
    assert calls == [True]


def test_assert_gate_raises_on_red():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"})
    with pytest.raises(GateAssertionError):
        assert_gate({"cid": "c1", "expect": [{"event": "missing"}]}, backend=b)


def test_assert_gate_accepts_an_explicit_evaluation_policy():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "done"}, {"event": "fault", "level": "ERROR"})
    spec = {"cid": "c1", "expect": [{"event": "done"}]}

    assert assert_gate(spec, backend=b)["ok"] is True
    with pytest.raises(GateAssertionError):
        assert_gate(spec, backend=b, policy=GatePolicy(forbid_errors=True))


def test_assert_present_forwards_an_explicit_evaluation_policy():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "done"}, {"event": "fault", "level": "ERROR"})

    with pytest.raises(GateAssertionError):
        assert_present(
            "c1",
            {"event": "done"},
            backend=b,
            policy=GatePolicy(forbid_errors=True),
        )


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
    with pytest.raises(GateAssertionError):
        assert_gate(
            {"cid": "c1", "expect": [{"event": "a"}]}, backend=_Unreachable(), strict_infra=True
        )
