"""Anti-flap confirm rounds — a revocable GREEN must survive re-reads to settle.

An irrevocable green (every gating check latched LTL3 SAT) early-settles as
before — monotone-positive evidence cannot be revoked, so confirming it is
waste. The flap risk lives in FINAL-path greens carrying revocable checks
(absent/forbid, exact counts, ...): the prefix looked clean, but a late-arriving
offender lands right after the last read. ``confirm_rounds=N`` re-reads N extra
times (``confirm_delay_s`` apart); any round that is no longer green wins —
the flap is caught, not raced.
"""
from __future__ import annotations

import time

import pytest

from ooptdd.backends.memory import reset
from ooptdd.config import Settings, from_mapping
from ooptdd.domain.ports import QueryResult
from ooptdd.engine.verify import verify_gate


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


class FakeClock:
    def __init__(self):
        self.us = int(time.time() * 1_000_000)

    def now_us(self):
        return self.us


class AdvancingSleeper:
    def __init__(self, clock):
        self.clock, self.calls = clock, []

    def __call__(self, seconds):
        self.calls.append(seconds)
        self.clock.us += int(seconds * 1_000_000)


class LateOffenderBackend:
    """Reads clean until ``offend_after_reads`` reads happened; then an ERROR event
    appears — the flap: a forbid-gate green that a re-read would revoke."""

    queryable = True
    default_lookback_s = 3600
    default_future_buffer_s = 300

    def __init__(self, offend_after_reads):
        self.reads = 0
        self.offend_after = offend_after_reads

    def query(self, cid, *, since_us, until_us):
        self.reads += 1
        events = [{"cid": cid, "event": "boot"}]
        if self.reads > self.offend_after:
            events.append({"cid": cid, "event": "boom", "level": "ERROR"})
        return QueryResult(reachable=True, events=events)


FORBID_SPEC = {"expect": [{"event": "boot", "op": ">=", "count": 1},
                          {"absent": {"where": {"level": "ERROR"}}}]}


def test_confirm_round_catches_the_late_offender():
    clock = FakeClock()
    backend = LateOffenderBackend(offend_after_reads=1)  # clean 1st read only
    res = verify_gate(backend, "c", FORBID_SPEC, retries=1, delay=0.1,
                      confirm_rounds=1, confirm_delay_s=0.5,
                      clock=clock, sleeper=AdvancingSleeper(clock))
    assert res["verdict"] == "absent" and res["ok"] is False
    assert res["arrival"]["confirm_rounds_run"] == 1


def test_without_confirm_the_flap_is_missed():
    clock = FakeClock()
    backend = LateOffenderBackend(offend_after_reads=1)
    res = verify_gate(backend, "c", FORBID_SPEC, retries=1, delay=0.1,
                      clock=clock, sleeper=AdvancingSleeper(clock))
    assert res["verdict"] == "present"  # the baseline race this feature closes
    assert res["arrival"]["confirm_rounds_run"] == 0


def test_stable_green_survives_all_confirm_rounds():
    clock = FakeClock()
    sleeper = AdvancingSleeper(clock)
    backend = LateOffenderBackend(offend_after_reads=99)
    res = verify_gate(backend, "c", FORBID_SPEC, retries=1, delay=0.1,
                      confirm_rounds=2, confirm_delay_s=0.5,
                      clock=clock, sleeper=sleeper)
    assert res["verdict"] == "present"
    assert res["arrival"]["confirm_rounds_run"] == 2
    assert sleeper.calls.count(0.5) == 2


def test_irrevocable_green_early_settles_without_confirm():
    # A monotone gate (only >= counts) latches SAT — early settle, no confirm reads.
    clock = FakeClock()
    backend = LateOffenderBackend(offend_after_reads=99)
    res = verify_gate(backend, "c", {"expect": [{"event": "boot", "op": ">=", "count": 1}]},
                      retries=3, delay=0.1, confirm_rounds=2, confirm_delay_s=0.5,
                      clock=clock, sleeper=AdvancingSleeper(clock))
    assert res["verdict"] == "present"
    assert res["arrival"]["confirm_rounds_run"] == 0
    assert backend.reads == 1  # settled on the first read; no confirm re-reads


def test_red_and_inconclusive_are_not_confirmed():
    # confirm is a GREEN-stability mechanism; a RED/? terminal needs no re-proof.
    clock = FakeClock()

    class Empty:
        queryable = True
        default_lookback_s = 3600
        default_future_buffer_s = 300

        def query(self, cid, *, since_us, until_us):
            return QueryResult(reachable=True, events=[])

    res = verify_gate(Empty(), "c", FORBID_SPEC, retries=1, delay=0.1,
                      confirm_rounds=3, confirm_delay_s=0.5,
                      clock=clock, sleeper=AdvancingSleeper(clock))
    assert res["verdict"] == "absent"
    assert res["arrival"]["confirm_rounds_run"] == 0


def test_settings_carry_confirm_fields():
    s = Settings()
    assert s.confirm_rounds == 0 and s.confirm_delay_s == 1.0
    s2 = from_mapping({"confirm_rounds": "2", "confirm_delay_s": "0.25"})
    assert s2.confirm_rounds == 2 and s2.confirm_delay_s == 0.25
