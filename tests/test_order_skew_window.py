"""Ordering ties under clock skew — `tie_skew_ms` + emitter-authoritative `_emit_seq`.

Cross-node wall clocks cannot prove order tighter than their skew, and a network
store's `_seq` is SERVER PAGE ORDER, not emit order. The verified rule, per pair:

1. both events carry `_emit_seq` (emitter-stamped) -> compare those, authoritative;
2. else if the check sets `tie_skew_ms: N` and the first-occurrence timestamps are
   within N ms -> the pair is CONCURRENT, not an inversion (never a false RED);
3. else the existing composite `(ts, _seq)` comparison stands.

memory/jsonl ship paths stamp `_emit_seq` themselves (their ship order IS emit
order); SUTs on network backends stamp it in their own envelopes to opt in.
"""
from __future__ import annotations

import pytest

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.engine.monitor import compile_check, run_monitor


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


def _rule(**kw):
    r = {"must_order": ["a", "b"]}
    r.update(kw)
    return r


def _ev(name, ts, seq=None, emit=None):
    e = {"event": name, "_timestamp": ts}
    if seq is not None:
        e["_seq"] = seq
    if emit is not None:
        e["_emit_seq"] = emit
    return e


# ── rule 2: the concurrency window ─────────────────────────────────────────────
def test_same_ms_server_order_inversion_within_window_is_not_red():
    # a's FIRST-OCCURRENCE ts is 200µs LATER than b's — a ts-order inversion, but
    # inside a 50ms skew window it is not PROVABLY one. Without tie_skew_ms it is
    # (and stays) a VIOL.
    events = [_ev("a", 1_000_200, seq=0), _ev("b", 1_000_000, seq=1)]
    strict = run_monitor(compile_check(_rule()), events, True)
    assert strict["passed"] is False
    lenient = run_monitor(compile_check(_rule(tie_skew_ms=50)), events, True)
    assert lenient["passed"] is True


def test_inversion_outside_window_is_still_red():
    events = [_ev("a", 2_000_000), _ev("b", 1_000_000)]  # a a full 1s after b
    res = run_monitor(compile_check(_rule(tie_skew_ms=50)), events, True)
    assert res["passed"] is False


def test_window_does_not_excuse_missing_names():
    res = run_monitor(compile_check(_rule(tie_skew_ms=50)), [_ev("a", 1)], True)
    assert res["passed"] is False and res["missing"] == ["b"]


# ── rule 1: emitter sequence is authoritative ──────────────────────────────────
def test_emit_seq_beats_server_order():
    # the ts order is INVERTED (a later than b); the emitters' own seq says a
    # first — and it is authoritative -> SAT.
    events = [_ev("a", 1_000_200, seq=0, emit=3), _ev("b", 1_000_000, seq=1, emit=7)]
    res = run_monitor(compile_check(_rule()), events, True)
    assert res["passed"] is True


def test_emit_seq_inversion_is_red_even_inside_the_window():
    # both stamped, and the emitters say the order was wrong: the window is for
    # UNPROVABLE ties, not for excusing a proven inversion.
    events = [_ev("a", 1_000_000, emit=9), _ev("b", 1_000_100, emit=2)]
    res = run_monitor(compile_check(_rule(tie_skew_ms=50)), events, True)
    assert res["passed"] is False


def test_one_sided_emit_seq_falls_back_to_window_then_composite():
    events = [_ev("a", 1_000_200, emit=1), _ev("b", 1_000_000)]  # only a stamped
    assert run_monitor(compile_check(_rule(tie_skew_ms=50)), events, True)["passed"] is True
    assert run_monitor(compile_check(_rule()), events, True)["passed"] is False


# ── memory ship path stamps _emit_seq ──────────────────────────────────────────
def test_memory_ship_stamps_monotonic_emit_seq():
    m = MemoryBackend()
    m.ship([{"cid": "c", "event": "a"}])
    m.ship([{"cid": "c", "event": "b"}])
    events = m.query("c", since_us=0, until_us=2**63 - 1).events
    seqs = [e.get("_emit_seq") for e in events]
    assert all(isinstance(s, int) for s in seqs)
    assert seqs == sorted(seqs) and len(set(seqs)) == 2


def test_ship_does_not_overwrite_a_sut_stamped_emit_seq():
    m = MemoryBackend()
    m.ship([{"cid": "c", "event": "a", "_emit_seq": 42}])
    events = m.query("c", since_us=0, until_us=2**63 - 1).events
    assert events[0]["_emit_seq"] == 42
