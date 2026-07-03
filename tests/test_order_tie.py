"""The ordering oracle must not be tie-blind at the mechanism level (audit gap-20).

ship() batch-stamps ONE wall-clock microsecond for every event in a call, and OrderMonitor
compared first-occurrence timestamps non-strictly — so two events shipped in one batch (or a
one-shot-replayed trace) shared a timestamp and passed must_order vacuously while still reading
ordered=True. A per-event monotonic _seq stamped by the backend breaks the wall-clock tie.
"""
import pytest

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.engine.gate import evaluate

_WIDE = dict(since_us=0, until_us=10 ** 19)


@pytest.fixture(autouse=True)
def _reset_store():
    reset()
    yield
    reset()


def _ck(cid):
    return {"cid": cid, "correlation_id": cid, "cycle_id": cid}


def test_same_batch_wrong_order_fails_must_order():
    """The defect: b then a in ONE ship() (same wall-clock ts) must NOT satisfy must_order[a, b]."""
    b = MemoryBackend()
    b.ship([{**_ck("t1"), "event": "b"}, {**_ck("t1"), "event": "a"}])
    res = evaluate(b, {"cid": "t1", "expect": [{"must_order": ["a", "b"]}]})
    assert res["ok"] is False
    assert res["checks"][0]["ordered"] is False


def test_same_batch_right_order_passes():
    b = MemoryBackend()
    b.ship([{**_ck("t2"), "event": "a"}, {**_ck("t2"), "event": "b"}])
    assert evaluate(b, {"cid": "t2", "expect": [{"must_order": ["a", "b"]}]})["ok"] is True


def test_seq_is_stamped_distinct_and_monotonic_through_query():
    b = MemoryBackend()
    b.ship([{**_ck("t3"), "event": "a"}, {**_ck("t3"), "event": "b"}])
    evs = b.query("t3", **_WIDE).events
    assert all("_seq" in e for e in evs)
    seqs = [e["_seq"] for e in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # distinct + monotonic


def test_single_event_stream_still_behaves():
    """Revert-proof: a fix that broke the single-event / no-pair case would red this."""
    b = MemoryBackend()
    b.ship([{**_ck("t4"), "event": "a"}])
    assert evaluate(b, {"cid": "t4", "expect": [{"must_order": ["a"]}]})["ok"] is True
