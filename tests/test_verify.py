"""The heart of ooptdd: the three-valued verdict and the policy on top of it."""
from ooptdd.backends import MemoryBackend
from ooptdd.backends.base import QueryResult
from ooptdd.model import build_outcome_records
from ooptdd.verify import session_finish, verify_policy, verify_trace


class _Unreachable:
    default_lookback_s = 60
    default_future_buffer_s = 0

    def ship(self, events):
        pass

    def query(self, cid, *, since_us, until_us):
        return QueryResult(reachable=False)


def test_present_when_session_shipped():
    b = MemoryBackend()
    b.ship(build_outcome_records(
        [{"nodeid": "t::a", "outcome": "passed", "when": "call"}],
        cid="c", service="x",
    ))
    v = verify_trace(b, "c", expect_total=1, retries=1)
    assert v["verdict"] == "present"
    assert v["ok"] is True


def test_absent_is_a_real_miss_not_inconclusive():
    # query reachable, but nothing stored (drop) -> ⊥ absent
    b = MemoryBackend(drop=True)
    b.ship(build_outcome_records(
        [{"nodeid": "t::a", "outcome": "passed", "when": "call"}], cid="c"))
    v = verify_trace(b, "c", expect_total=1, retries=1)
    assert v["verdict"] == "absent"
    assert v["ok"] is False


def test_inconclusive_when_store_unreachable():
    v = verify_trace(_Unreachable(), "c", expect_total=1, retries=1)
    assert v["verdict"] == "inconclusive"


def test_partial_loss_detected():
    # session says total=3 but only 1 outcome arrived -> not ok (partial loss)
    b = MemoryBackend()
    b.ship([
        {"cid": "c", "event": "test_session", "total": 3, "passed": 3, "service": "x"},
        {"cid": "c", "event": "test_outcome", "outcome": "passed"},
    ])
    v = verify_trace(b, "c", expect_total=3, retries=1)
    assert v["verdict"] == "present"
    assert v["ok"] is False
    assert any("partial_loss" in r for r in v["reasons"])


def test_policy_strict_fails_only_on_absent():
    absent = {"ok": False, "verdict": "absent", "reasons": ["x"]}
    incon = {"ok": False, "verdict": "inconclusive", "reasons": ["x"]}
    assert verify_policy(absent, "strict")["fail_build"] is True
    assert verify_policy(absent, "warn")["fail_build"] is False
    # inconclusive never fails the build, even strict (? is not ⊥)
    assert verify_policy(incon, "strict")["fail_build"] is False


def test_session_finish_strict_catches_silent_loss():
    reports = [{"nodeid": f"t::{i}", "outcome": "passed", "when": "call"} for i in range(5)]
    # 5 tests "shipped" but the backend silently drops them
    r = session_finish(MemoryBackend(drop=True), reports, "cid-loss",
                       mode="strict", retries=1)
    assert r["fail_build"] is True
    assert any("silent ingest loss" in m for m in r["messages"])


def test_session_finish_green_when_arrives():
    reports = [{"nodeid": f"t::{i}", "outcome": "passed", "when": "call"} for i in range(5)]
    r = session_finish(MemoryBackend(), reports, "cid-ok", mode="strict", retries=1)
    assert r["fail_build"] is False
    assert any("arrival confirmed" in m for m in r["messages"])
