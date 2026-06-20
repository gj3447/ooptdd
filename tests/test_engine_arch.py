"""Engine architecture: typed ports (Clock/TimeWindow/QuerySpec/BackendCaps), the public
kernel compile API (compile_check + LiveMonitorSet), and the explicit BackendRegistry.

These lock the refinements that make the engine deterministically testable, give live/
resident mode a first-class entry point, and make backend resolution injectable — all
additive, with the dependency direction unchanged.
"""
from __future__ import annotations

import pytest

from ooptdd.backends import BackendRegistry, MemoryBackend
from ooptdd.domain.ports import (
    BackendCaps,
    QuerySpec,
    SystemClock,
    TimeWindow,
    backend_caps,
    fetch,
)
from ooptdd.engine.monitor import (
    SAT,
    VIOL,
    AbsentMonitor,
    CountMonitor,
    LiveMonitorSet,
    OrderMonitor,
    PresentMonitor,
    compile_check,
    run_monitor,
)


def _ev(name, ts=None, **kw):
    e = {"event": name, **kw}
    if ts is not None:
        e["_timestamp"] = ts
    return e


# ── Clock / TimeWindow: deterministic, exact arithmetic ────────────────────────
class _FixedClock:
    def now_us(self):
        return 5_000_000_000


def test_timewindow_around_now_matches_legacy_arithmetic():
    w = TimeWindow.around_now(_FixedClock(), lookback_s=3600, future_buffer_s=300)
    now = 5_000_000_000
    assert w.since_us == now - 3600 * 1_000_000
    assert w.until_us == now + 300 * 1_000_000


def test_system_clock_is_microseconds():
    assert SystemClock().now_us() > 1_000_000_000_000_000  # well past 2001 in µs


# ── compile_check: rule -> the right Monitor (single source of truth) ──────────
@pytest.mark.parametrize("rule, cls", [
    ({"event": "a", "op": ">=", "count": 1}, CountMonitor),
    ({"present": [{"event": "a"}]}, PresentMonitor),
    ({"absent": {"where": {"level": "ERROR"}}}, AbsentMonitor),
    ({"forbid": {"where": {"level": "ERROR"}}}, AbsentMonitor),   # synonym
    ({"must_order": ["a", "b"]}, OrderMonitor),
    ({"trajectory": ["a", "b"]}, OrderMonitor),                  # synonym
])
def test_compile_check_picks_the_right_monitor(rule, cls):
    assert isinstance(compile_check(rule), cls)


def test_compile_check_equals_batch_handler_output():
    # the monitor compile_check builds, run over events, equals what gate produces
    from ooptdd.backends.memory import reset
    from ooptdd.engine.gate import evaluate
    reset()
    b = MemoryBackend()
    b.ship([{"cid": "c", "event": "a"}, {"cid": "c", "event": "a"}])
    rule = {"event": "a", "op": ">=", "count": 2}
    gate_chk = evaluate(b, {"cid": "c", "expect": [rule]})["checks"][0]
    direct = run_monitor(compile_check(rule), [_ev("a"), _ev("a")], reachable=True)
    assert direct["got"] == gate_chk["got"] == 2 and direct["passed"] == gate_chk["passed"]
    reset()


# ── LiveMonitorSet: the live path equals the batch path ────────────────────────
def test_live_monitor_set_matches_batch():
    rules = [{"event": "a", "op": ">=", "count": 2}, {"present": [{"event": "b"}]}]
    stream = [_ev("a", 1), _ev("b", 2), _ev("a", 3)]
    live = LiveMonitorSet.from_rules(rules)
    for ev in stream:
        live.feed(ev)
    assert live.verdicts() == [SAT, SAT]                  # count>=2 reached; b present
    collapsed = live.collapse(reachable=True)
    assert collapsed[0]["got"] == 2 and collapsed[1]["passed"] is True


def test_live_monitor_set_latches_viol_incrementally():
    live = LiveMonitorSet.from_rules([{"absent": {"where": {"level": "ERROR"}}}])
    live.feed(_ev("ok"))
    assert live.verdicts() == ["pend"]
    live.feed(_ev("boom", level="ERROR"))
    assert live.verdicts() == [VIOL]                       # latched on the first offender


# ── BackendCaps / backend_caps bridge ──────────────────────────────────────────
def test_backend_caps_reads_caps_when_present():
    caps = backend_caps(MemoryBackend())
    assert isinstance(caps, BackendCaps) and caps.queryable and caps.supports_where


def test_backend_caps_synthesizes_from_legacy_queryable():
    class _Legacy:
        queryable = False
    caps = backend_caps(_Legacy())
    assert caps.queryable is False and caps.write_only is True


# ── fetch shim: typed QuerySpec over legacy and query_spec backends ────────────
def test_fetch_drives_a_legacy_query_only_backend():
    from ooptdd.backends.memory import reset
    reset()
    b = MemoryBackend()
    b.ship([{"cid": "c", "event": "a"}])
    spec = QuerySpec(cid="c", window=TimeWindow(0, 10**19))
    res = fetch(b, spec)
    assert res.reachable and [e["event"] for e in res.events] == ["a"]
    reset()


def test_fetch_prefers_query_spec_when_present():
    class _SpecBackend:
        default_lookback_s = 1
        default_future_buffer_s = 0
        called = {}

        def ship(self, events):  # pragma: no cover
            pass

        def query(self, cid, *, since_us, until_us):  # pragma: no cover
            raise AssertionError("should have used query_spec")

        def query_spec(self, spec):
            self.called["cid"] = spec.cid
            from ooptdd.backends.base import QueryResult
            return QueryResult(reachable=True, events=[{"event": "z"}])

    b = _SpecBackend()
    res = fetch(b, QuerySpec(cid="c9", window=TimeWindow(0, 1)))
    assert b.called["cid"] == "c9" and res.events[0]["event"] == "z"


# ── BackendRegistry: explicit + injectable ─────────────────────────────────────
def test_registry_resolves_builtins_and_register_unregister():
    reg = BackendRegistry()
    assert "memory" in reg.names()
    assert isinstance(reg.resolve("memory"), MemoryBackend)

    sentinel = object()
    reg.register("fake", lambda **o: sentinel)
    assert "fake" in reg.names() and reg.resolve("fake") is sentinel
    reg.unregister("fake")
    assert "fake" not in reg.names()


def test_registry_unknown_name_raises():
    with pytest.raises(ValueError, match="unknown ooptdd backend"):
        BackendRegistry().resolve("nope-not-a-backend")
