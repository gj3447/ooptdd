"""verify is built on the generic streaming monitor, not a pytest-only path.

`poll_until_present` is the shape-agnostic arrival loop; `verify_gate` verifies that an
*arbitrary* gate spec (any domain events, by cid) eventually arrives — present/absent/
inconclusive — reusing the exact gate monitor dispatch. A fake clock + sleeper make the
retry loop deterministic with no real delay.
"""
from __future__ import annotations

from ooptdd.backends.base import QueryResult
from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.engine.gate import evaluate
from ooptdd.engine.verify import poll_until_present, verify_gate, verify_trace


class _FakeClock:
    def __init__(self):
        self.t = 1_000_000_000

    def now_us(self):
        self.t += 1
        return self.t


def _no_sleep(_):
    pass


# ── a programmable backend: reachable/empty on the first k polls, then the events ──
class _LateBackend:
    default_lookback_s = 3600
    default_future_buffer_s = 0

    def __init__(self, *, ready_on, events, reachable=True):
        self.calls = 0
        self.ready_on = ready_on
        self._events = events
        self._reachable = reachable

    def ship(self, events):  # pragma: no cover
        pass

    def query(self, cid, *, since_us, until_us):
        self.calls += 1
        if not self._reachable:
            return QueryResult(reachable=False)
        evs = self._events if self.calls >= self.ready_on else []
        return QueryResult(reachable=True, events=evs)


# ── poll_until_present: the generic loop ───────────────────────────────────────
def test_poll_stops_as_soon_as_callback_settles():
    b = _LateBackend(ready_on=3, events=[{"event": "x", "_timestamp": 1}])

    def found(events, *, reachable, complete, queried_ok, attempt, final):
        return {"hit": True} if any(e["event"] == "x" for e in events) else None

    out = poll_until_present(b, "c", found, retries=5, clock=_FakeClock(), sleeper=_no_sleep)
    assert out["hit"] and out["attempts"] == 3 and b.calls == 3


def test_poll_final_distinguishes_absent_from_inconclusive():
    seen = {}

    def cb(events, *, reachable, complete, queried_ok, attempt, final):
        if final:
            seen["queried_ok"] = queried_ok
            return {"verdict": "absent" if queried_ok else "inconclusive"}
        return None

    # reachable but empty -> absent (⊥)
    out = poll_until_present(_LateBackend(ready_on=99, events=[]), "c", cb,
                             retries=2, clock=_FakeClock(), sleeper=_no_sleep)
    assert out["verdict"] == "absent" and out["attempts"] == 2
    # never reachable -> inconclusive (?)
    out = poll_until_present(_LateBackend(ready_on=1, events=[], reachable=False), "c", cb,
                             retries=2, clock=_FakeClock(), sleeper=_no_sleep)
    assert out["verdict"] == "inconclusive"


# ── verify_gate: arbitrary domain events, by cid ───────────────────────────────
def test_verify_gate_present_for_arbitrary_domain_events():
    reset()
    b = MemoryBackend()
    b.ship([{"cid": "c", "event": "cycle", "verdict": "PASS"}])
    spec = {"expect": [{"event": "cycle", "where": {"verdict": "PASS"}, "op": "==", "count": 1}]}
    # real clock: MemoryBackend stamps real wall-clock time, so the readback window must be
    # the real now (a 1970-ish fake clock would filter the just-shipped event out).
    out = verify_gate(b, "c", spec, retries=1, sleeper=_no_sleep)
    assert out["ok"] and out["verdict"] == "present"
    # the poll's verdict agrees with a one-shot gate evaluation over the same store
    assert evaluate(b, {"cid": "c", **spec})["ok"] is True
    reset()


def test_verify_gate_absent_when_events_never_arrive():
    reset()
    b = MemoryBackend()  # nothing shipped
    spec = {"expect": [{"event": "cycle", "op": ">=", "count": 1}]}
    out = verify_gate(b, "c", spec, retries=2, sleeper=_no_sleep)
    assert not out["ok"] and out["verdict"] == "absent"
    assert "cycle" in out["reasons"]
    reset()


def test_verify_gate_inconclusive_on_unreachable_store():
    spec = {"expect": [{"event": "cycle", "op": ">=", "count": 1}]}
    out = verify_gate(_LateBackend(ready_on=1, events=[], reachable=False), "c", spec,
                      retries=2, clock=_FakeClock(), sleeper=_no_sleep)
    assert not out["ok"] and out["verdict"] == "inconclusive"


# ── deterministic: no real sleep, window advances per poll via the injected clock ──
def test_verify_trace_is_deterministic_under_fake_clock():
    sleeps: list[float] = []
    b = _LateBackend(ready_on=99, events=[])  # never finds a session -> exhausts retries
    out = verify_trace(b, "c", retries=4, clock=_FakeClock(), sleeper=sleeps.append)
    assert out["verdict"] == "absent" and out["attempts"] == 4
    assert len(sleeps) == 3  # immediate first poll, then a wait before each of attempts 2..4
