"""ArrivalPolicy — the category-killer fix, without giving up the product.

Trace-based testing died by conflating ingestion lag with absence (timeout=fail).
ooptdd's core claim is the opposite of that mistake AND must keep catching real
silent loss, so the policy is surgical:

- a backend DECLARES its query-visibility delay (``BackendCaps.query_visibility_delay_ms``,
  from its official docs);
- the poller never concludes ABSENT while the total wait is still inside that blind
  window — if the budget ran out early it extends once past the window (bounded);
- past the window, a reachable+complete empty read is still ABSENT = RED. That is
  the product, not a bug;
- a backend may expose ``force_flush()`` (VictoriaLogs documents
  ``POST /internal/force_flush`` for exactly this) — the poller calls it once,
  best-effort, before the first read.
"""
from __future__ import annotations

import time

import pytest

from ooptdd.backends.clickhouse import ClickHouseBackend
from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.backends.openobserve import OpenObserveBackend
from ooptdd.backends.victorialogs import VictoriaLogsBackend
from ooptdd.domain.ports import BackendCaps, QueryResult
from ooptdd.engine.verify import verify_gate


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


class FakeClock:
    """Deterministic clock anchored at the real epoch (so real-stamped events — e.g.
    memory-backend `_timestamp`s — fall inside the poll window)."""

    def __init__(self):
        self.us = int(time.time() * 1_000_000)

    def now_us(self):
        return self.us


class AdvancingSleeper:
    """A sleeper that advances the fake clock — deterministic waiting."""

    def __init__(self, clock):
        self.clock = clock
        self.calls: list[float] = []

    def __call__(self, seconds):
        self.calls.append(seconds)
        self.clock.us += int(seconds * 1_000_000)


class LaggyBackend:
    """Reachable store whose events only become visible after ``visible_after_us``
    (ingestion lag). The regression this suite pins: without the blind-window guard
    a short poll budget reads ABSENT inside the lag window — a fake RED."""

    queryable = True
    default_lookback_s = 3600
    default_future_buffer_s = 300

    def __init__(self, clock, events, visible_after_us, visibility_ms):
        self.clock, self.events, self.visible_after_us = clock, events, visible_after_us
        self.caps = BackendCaps(queryable=True, supports_where=True,
                                query_visibility_delay_ms=visibility_ms)
        self.flushes = 0

    def ship(self, events):  # pragma: no cover - unused
        self.events.extend(events)

    def force_flush(self):
        self.flushes += 1
        return True

    def query(self, cid, *, since_us, until_us):
        visible = (self.events if self.clock.now_us() >= self.visible_after_us else [])
        return QueryResult(reachable=True,
                           events=[e for e in visible if e.get("cid") == cid])


SPEC = {"expect": [{"event": "a", "op": ">=", "count": 1}]}


def _laggy(clock, *, lag_ms=800, visibility_ms=1000, events=None):
    evs = events if events is not None else [{"cid": "c", "event": "a"}]
    return LaggyBackend(clock, evs, clock.us + lag_ms * 1000, visibility_ms)


# ── the blind-window guard ─────────────────────────────────────────────────────
def test_lagged_arrival_inside_blind_window_is_not_absent():
    # Budget: 1 attempt, no sleeps -> waited 0ms < declared 1000ms window. The old
    # behavior settled absent RED here; the guard extends past the window and finds
    # the events: GREEN, with the extension visible in the arrival metadata.
    clock = FakeClock()
    sleeper = AdvancingSleeper(clock)
    backend = _laggy(clock)
    res = verify_gate(backend, "c", SPEC, retries=1, delay=0.1,
                      clock=clock, sleeper=sleeper)
    assert res["verdict"] == "present" and res["ok"] is True
    assert res["arrival"]["extended_for_visibility"] is True
    assert res["arrival"]["visibility_delay_ms"] == 1000


def test_truly_absent_past_the_window_is_still_red():
    # The product: once the wait provably covered the blind window, a reachable
    # empty read IS absence. No inconclusive cop-out.
    clock = FakeClock()
    sleeper = AdvancingSleeper(clock)
    backend = _laggy(clock, events=[], visibility_ms=1000)
    res = verify_gate(backend, "c", SPEC, retries=3, delay=1.0,
                      clock=clock, sleeper=sleeper)
    assert res["verdict"] == "absent" and res["ok"] is False
    assert res["arrival"]["waited_ms"] >= 1000


def test_no_extension_when_budget_already_covered_the_window():
    clock = FakeClock()
    sleeper = AdvancingSleeper(clock)
    backend = _laggy(clock, events=[], visibility_ms=500)
    res = verify_gate(backend, "c", SPEC, retries=3, delay=1.0,
                      clock=clock, sleeper=sleeper)
    # retries=3 with delay 1.0 sleeps ~1s+2s -> way past 500ms: no extension round
    assert res["arrival"]["extended_for_visibility"] is False


def test_memory_backend_declares_zero_window_no_behavior_change():
    # Ship BEFORE freezing the clock: memory's future_buffer is 0, so a frozen clock
    # created first can lose a µs race against the real-time ship stamp (the event
    # would sit "in the future" of the frozen window — a fixture artifact, not product).
    MemoryBackend().ship([{"cid": "m", "event": "a"}])
    clock = FakeClock()
    sleeper = AdvancingSleeper(clock)
    res = verify_gate(MemoryBackend(), "m", SPEC, retries=1, delay=0.1,
                      clock=clock, sleeper=sleeper)
    assert res["verdict"] == "present"
    assert res["arrival"]["visibility_delay_ms"] == 0
    assert res["arrival"]["extended_for_visibility"] is False


# ── force_flush hook ───────────────────────────────────────────────────────────
def test_force_flush_called_once_before_first_read():
    clock = FakeClock()
    sleeper = AdvancingSleeper(clock)
    backend = _laggy(clock, lag_ms=0)
    res = verify_gate(backend, "c", SPEC, retries=1, delay=0.1,
                      clock=clock, sleeper=sleeper)
    assert backend.flushes == 1
    assert res["arrival"]["flushed"] is True


def test_force_flush_failure_is_swallowed_not_fatal():
    clock = FakeClock()
    sleeper = AdvancingSleeper(clock)
    backend = _laggy(clock, lag_ms=0)

    def boom():
        raise OSError("flush endpoint 503")

    backend.force_flush = boom
    res = verify_gate(backend, "c", SPEC, retries=1, delay=0.1,
                      clock=clock, sleeper=sleeper)
    assert res["verdict"] == "present"  # flush is best-effort, never the verdict
    assert res["arrival"]["flushed"] is False


# ── declared caps: the matrix becomes explicit ─────────────────────────────────
def test_backend_caps_field_defaults_to_zero():
    assert BackendCaps().query_visibility_delay_ms == 0


def test_network_backends_declare_official_visibility_delays():
    oo = OpenObserveBackend(base_url="http://x", org="o", stream="s")
    ch = ClickHouseBackend(base_url="http://x")
    vl = VictoriaLogsBackend(base_url="http://x")
    assert oo.caps.query_visibility_delay_ms == 5000   # memtable/WAL persist interval
    assert ch.caps.query_visibility_delay_ms == 1000   # async_insert busy timeout band
    assert vl.caps.query_visibility_delay_ms == 1000   # docs: data becomes searchable ~1s
    assert oo.caps.queryable and ch.caps.queryable and vl.caps.queryable
    assert oo.caps.paginates is True  # OO driver pages to completion; CH/VL single-shot
    assert ch.caps.paginates is False and vl.caps.paginates is False


def test_victorialogs_force_flush_posts_the_documented_endpoint(monkeypatch):
    monkeypatch.setenv("OOPTDD_VL_URL", "http://vl:9428")
    seen = {}

    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()

        class R:
            def read(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    vl = VictoriaLogsBackend(opener=opener)
    assert vl.force_flush() is True
    assert seen["url"].endswith("/internal/force_flush") and seen["method"] == "POST"
