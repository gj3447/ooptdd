"""verify is built on the generic streaming monitor, not a pytest-only path.

`poll_until_present` is the shape-agnostic arrival loop; `verify_gate` verifies that an
*arbitrary* gate spec (any domain events, by cid) eventually arrives — present/absent/
inconclusive — reusing the exact gate monitor dispatch. A fake clock + sleeper make the
retry loop deterministic with no real delay.
"""

from __future__ import annotations

from ooptdd.adapters.pytest import session_finish, verify_trace
from ooptdd.backends.base import QueryResult
from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.domain.settings import PollingSettings
from ooptdd.engine.gate import evaluate
from ooptdd.engine.gate_values import GatePolicy
from ooptdd.engine.polling import next_poll_delay, resolve_polling_settings
from ooptdd.engine.verify import poll_until_present, verify_gate


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
    out = poll_until_present(
        _LateBackend(ready_on=99, events=[]),
        "c",
        cb,
        retries=2,
        clock=_FakeClock(),
        sleeper=_no_sleep,
    )
    assert out["verdict"] == "absent" and out["attempts"] == 2
    # never reachable -> inconclusive (?)
    out = poll_until_present(
        _LateBackend(ready_on=1, events=[], reachable=False),
        "c",
        cb,
        retries=2,
        clock=_FakeClock(),
        sleeper=_no_sleep,
    )
    assert out["verdict"] == "inconclusive"


def test_polling_policy_is_immutable_and_scalar_compatibility_is_one_merge():
    policy = PollingSettings(
        retries=3,
        delay=0.25,
        backoff=3.0,
        max_delay=1.0,
        confirm_rounds=2,
        confirm_delay_s=0.5,
    )
    resolved = resolve_polling_settings(policy, retries=5, confirm_rounds=0)

    assert policy.retries == 3 and policy.confirm_rounds == 2
    assert resolved == PollingSettings(
        retries=5,
        delay=0.25,
        backoff=3.0,
        max_delay=1.0,
        confirm_rounds=0,
        confirm_delay_s=0.5,
    )
    assert next_poll_delay(policy, 1) == 0.25
    assert next_poll_delay(policy, 3) == 1.0
    assert next_poll_delay(policy, 1, retry_after_s=2.0) == 2.0


def test_poll_until_present_uses_the_complete_policy_object():
    sleeps: list[float] = []
    policy = PollingSettings(
        retries=3,
        delay=0.25,
        backoff=2.0,
        max_delay=0.4,
        confirm_rounds=0,
        confirm_delay_s=0.75,
    )

    def final_only(events, *, reachable, complete, queried_ok, attempt, final):
        return {"verdict": "absent"} if final else None

    out = poll_until_present(
        _LateBackend(ready_on=99, events=[]),
        "c",
        final_only,
        polling=policy,
        clock=_FakeClock(),
        sleeper=sleeps.append,
    )

    assert out["attempts"] == 3
    assert sleeps == [0.25, 0.4]


def test_all_verification_entry_points_forward_the_same_policy():
    policy = PollingSettings(
        retries=3,
        delay=0.25,
        backoff=2.0,
        max_delay=0.4,
        confirm_rounds=0,
        confirm_delay_s=0.75,
    )

    trace_sleeps: list[float] = []
    trace = verify_trace(
        _LateBackend(ready_on=99, events=[]),
        "trace",
        polling=policy,
        clock=_FakeClock(),
        sleeper=trace_sleeps.append,
    )
    assert trace["attempts"] == 3 and trace_sleeps == [0.25, 0.4]

    gate_sleeps: list[float] = []
    gate = verify_gate(
        _LateBackend(ready_on=99, events=[]),
        "gate",
        {"expect": [{"event": "ready", "op": ">=", "count": 1}]},
        polling=policy,
        clock=_FakeClock(),
        sleeper=gate_sleeps.append,
    )
    assert gate["attempts"] == 3 and gate_sleeps == [0.25, 0.4]

    finish_sleeps: list[float] = []
    finished = session_finish(
        MemoryBackend(drop=True),
        [{"nodeid": "test_x", "outcome": "passed", "when": "call"}],
        "finish",
        mode="strict",
        polling=policy,
        sleeper=finish_sleeps.append,
    )
    assert finished["fail_build"] is True
    assert finish_sleeps == [0.25, 0.4]


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
    out = verify_gate(
        _LateBackend(ready_on=1, events=[], reachable=False),
        "c",
        spec,
        retries=2,
        clock=_FakeClock(),
        sleeper=_no_sleep,
    )
    assert not out["ok"] and out["verdict"] == "inconclusive"


# ── audit residual #1 (forgery-paths audit 2026-07-08): no early settle on a merely
# violation-free-so-far prefix. A non-final poll sees a PREFIX of the trace; a gate with an
# anti-monotone check (absent/forbid, exact/upper-bound counts, heartbeat, ratio, invariant,
# metamorphic, conforms, external, custom) passes vacuously/provisionally on that prefix, so
# settling 'present' there lets a late-arriving violation miss the final verdict. The kernel
# already knows which greens are irrevocable: only monotone-positive automata latch LTL₃ SAT.


class _GrowingBackend:
    """Programmable per-poll snapshots: poll k returns ``snapshots[min(k, len)-1]`` —
    the arrival-race simulator (events that land AFTER an early poll)."""

    default_lookback_s = 3600
    default_future_buffer_s = 0

    def __init__(self, snapshots):
        self.calls = 0
        self.snapshots = snapshots

    def ship(self, events):  # pragma: no cover
        pass

    def query(self, cid, *, since_us, until_us):
        self.calls += 1
        return QueryResult(
            reachable=True,
            events=self.snapshots[min(self.calls, len(self.snapshots)) - 1],
        )


def test_verify_gate_late_violation_flips_a_forbid_gate_red():
    """A gate with a forbid/absent wing must NOT settle 'present' on a non-final poll whose
    prefix merely has no offender YET — the late offender must flip the final verdict."""
    spec = {
        "expect": [
            {"event": "cycle", "op": ">=", "count": 1},
            {"absent": [{"where": {"level": "ERROR"}}]},
        ]
    }
    b = _GrowingBackend(
        [
            [{"event": "cycle", "_timestamp": 1}],  # poll 1: green-so-far prefix
            [
                {"event": "cycle", "_timestamp": 1},
                {"event": "boom", "level": "ERROR", "_timestamp": 2},
            ],  # poll 2+: offender arrives
        ]
    )
    out = verify_gate(b, "c", spec, retries=3, clock=_FakeClock(), sleeper=_no_sleep)
    assert not out["ok"] and out["verdict"] == "absent"
    assert out["attempts"] == 3  # waited for the final poll instead of settling at 1


def test_verify_gate_late_duplicate_flips_an_exact_count_gate_red():
    """`== 1` is anti-monotone too: one more match on a later poll breaks it."""
    spec = {"expect": [{"event": "cycle", "op": "==", "count": 1}]}
    b = _GrowingBackend(
        [
            [{"event": "cycle", "_timestamp": 1}],
            [{"event": "cycle", "_timestamp": 1}, {"event": "cycle", "_timestamp": 2}],
        ]
    )
    out = verify_gate(b, "c", spec, retries=2, clock=_FakeClock(), sleeper=_no_sleep)
    assert not out["ok"] and out["verdict"] == "absent" and out["attempts"] == 2


def test_verify_gate_green_anti_monotone_gate_waits_for_the_final_poll():
    """Even a gate that STAYS green must not settle early when it carries an anti-monotone
    check — its green is only confident once no more polls remain."""
    spec = {
        "expect": [
            {"event": "cycle", "op": ">=", "count": 1},
            {"absent": [{"where": {"level": "ERROR"}}]},
        ]
    }
    b = _GrowingBackend([[{"event": "cycle", "_timestamp": 1}]])
    out = verify_gate(b, "c", spec, retries=3, clock=_FakeClock(), sleeper=_no_sleep)
    assert out["ok"] and out["verdict"] == "present" and out["attempts"] == 3


def test_verify_gate_still_settles_early_when_every_check_is_monotone():
    """The performance path survives: a gate of only genuinely monotone-positive checks
    (>= count, present) latches SAT and may settle the moment the prefix satisfies it.
    (must_order is NOT here — its SAT is reorder-revocable under ingest-order polling, see
    test_verify_gate_never_early_settles_an_order_check.)"""
    spec = {
        "expect": [
            {"event": "cycle", "op": ">=", "count": 1},
            {"present": [{"event": "cycle"}]},
        ]
    }
    events = [{"event": "start", "_timestamp": 1}, {"event": "cycle", "_timestamp": 2}]
    b = _LateBackend(ready_on=2, events=events)
    out = verify_gate(b, "c", spec, retries=5, clock=_FakeClock(), sleeper=_no_sleep)
    assert out["ok"] and out["verdict"] == "present"
    assert out["attempts"] == 2 and b.calls == 2  # settled as soon as the events arrived


def test_verify_gate_never_early_settles_an_order_check():
    """grill F1: must_order/trajectory SAT is only valid for timestamp-ordered extensions,
    but polls arrive in INGEST order — a late event with an earlier timestamp can flip it.
    So a gate with a gating order check must poll to the final window, never settle early."""
    from ooptdd.engine.gate import evaluate_events
    from ooptdd.engine.verify import _settled_green

    # a prefix where a@1 then b@2 satisfies must_order[a,b] in the batch (sorted) view...
    prefix = [
        {"event": "a", "cid": "c", "_timestamp": 10},
        {"event": "b", "cid": "c", "_timestamp": 20},
    ]
    res = evaluate_events(
        {"cid": "c", "expect": [{"must_order": ["a", "b"]}]}, prefix, reachable=True, cid="c"
    )
    assert res["ok"] and not _settled_green(res)  # green now, but NOT safe to settle
    # ...because a later-ingested b@5 (earlier ts) would flip the full-window verdict RED
    full = [*prefix, {"event": "b", "cid": "c", "_timestamp": 5}]
    assert not evaluate_events(
        {"cid": "c", "expect": [{"must_order": ["a", "b"]}]}, full, reachable=True, cid="c"
    )["ok"]


def test_verify_gate_fail_closed_for_a_check_without_a_kernel_verdict():
    """A check kind of unknown monotonicity (a custom @check predicate carries no kernel
    LTL₃ verdict) must be treated as revocable: no early settle (fail-closed)."""
    from ooptdd.engine.gate import check, checks_from, compose_check_registry

    @check("always_green")
    def _custom(events, rule, ctx):
        return {"passed": True}

    registry = compose_check_registry(checks_from(_custom))
    spec = {"expect": [{"always_green": True}]}
    b = _GrowingBackend([[{"event": "cycle", "_timestamp": 1}]])
    out = verify_gate(
        b,
        "c",
        spec,
        retries=3,
        clock=_FakeClock(),
        sleeper=_no_sleep,
        registry=registry,
    )
    assert out["ok"] and out["verdict"] == "present" and out["attempts"] == 3


def test_verify_gate_signature_enforcement_forbids_early_settle():
    """require_signature verifies the WHOLE hash chain — a later forged event still breaks
    it, so an all-SAT prefix under signature enforcement must not settle early either."""
    from ooptdd.domain.model import sign_chain

    signed = sign_chain([{"cid": "c", "event": "cycle"}], "k1")
    signed[0]["_timestamp"] = 1
    forged = {"cid": "c", "event": "cycle", "_timestamp": 2}  # off-chain injection
    spec = {"require_signature": True, "expect": [{"event": "cycle", "op": ">=", "count": 1}]}
    b = _GrowingBackend([signed, signed + [forged]])
    out = verify_gate(
        b,
        "c",
        spec,
        retries=2,
        clock=_FakeClock(),
        sleeper=_no_sleep,
        policy=GatePolicy(require_signature=True, signing_key="k1"),
    )
    assert not out["ok"] and out["attempts"] == 2
    assert out["gate"]["unauthenticated"] is True


# ── deterministic: no real sleep, window advances per poll via the injected clock ──
def test_verify_trace_is_deterministic_under_fake_clock():
    sleeps: list[float] = []
    b = _LateBackend(ready_on=99, events=[])  # never finds a session -> exhausts retries
    out = verify_trace(b, "c", retries=4, clock=_FakeClock(), sleeper=sleeps.append)
    assert out["verdict"] == "absent" and out["attempts"] == 4
    assert len(sleeps) == 3  # immediate first poll, then a wait before each of attempts 2..4
