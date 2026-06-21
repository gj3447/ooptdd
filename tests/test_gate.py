"""gate.evaluate — count + `where` field-filter (and, added later, must_order/optional).

Everything is exercised over the in-memory backend (zero infra); the OpenObserve
driver is checked only for whole-row passthrough (the seam #11 needs).
"""
from __future__ import annotations

import json

import pytest

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.engine.gate import evaluate


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


def _ship(backend, cid, *events):
    backend.ship([{"cid": cid, **e} for e in events])


# ── existing behaviour: count by event ────────────────────────────────────────
def test_event_count_gate_pass():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "test_session", "total": 3},
          {"event": "test_outcome"}, {"event": "test_outcome"}, {"event": "test_outcome"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"event": "test_session", "op": ">=", "count": 1},
        {"event": "test_outcome", "op": ">=", "count": 3},
    ]})
    assert res["ok"] and res["reachable"] and all(c["passed"] for c in res["checks"])


def test_event_count_gate_fail():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "test_outcome"})
    res = evaluate(b, {"cid": "c1", "expect": [{"event": "test_outcome", "op": ">=", "count": 5}]})
    assert not res["ok"] and res["checks"][0]["got"] == 1


# ── #11 field-filter (`where`) ────────────────────────────────────────────────
def test_where_filter_counts_only_matching_field():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle", "verdict": "PASS"},
          {"event": "cycle", "verdict": "NG"}, {"event": "cycle", "verdict": "PASS"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"event": "cycle", "where": {"verdict": "NG"}, "op": "==", "count": 1},
    ]})
    assert res["ok"] and res["checks"][0]["got"] == 1


def test_where_filter_event_omitted_matches_any_event():
    # jg_bpc `WHERE level='ERROR'` pattern — no event name, pure field filter.
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a", "level": "ERROR"}, {"event": "b", "level": "INFO"},
          {"event": "c", "level": "ERROR"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"where": {"level": "ERROR"}, "op": "==", "count": 2},
    ]})
    assert res["ok"] and res["checks"][0]["got"] == 2


def test_where_filter_ng_zero_gate():
    # jg_bpc `WHERE verdict='NG'` == 0 (no NG cycles) — the canonical field-filter gate.
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle", "verdict": "PASS"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"event": "cycle", "where": {"verdict": "NG"}, "op": "==", "count": 0},
    ]})
    assert res["ok"]


def test_where_filter_multi_field_and_semantics():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle", "verdict": "NG", "station": "A"},
          {"event": "cycle", "verdict": "NG", "station": "B"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"event": "cycle", "where": {"verdict": "NG", "station": "A"}, "op": "==", "count": 1},
    ]})
    assert res["ok"] and res["checks"][0]["got"] == 1


# ── #11 seam: OpenObserve returns whole rows so `where` fields are present ──────
def test_openobserve_select_star_passes_through_arbitrary_fields(monkeypatch):
    from ooptdd.backends.openobserve import OpenObserveBackend

    monkeypatch.setenv("OOPTDD_OO_URL", "http://oo.test:5080")
    monkeypatch.setenv("OOPTDD_OO_PASSWORD", "x")
    captured: dict = {}

    class _R:
        def __init__(self, body):
            self._b = body

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        captured["sql"] = json.loads(req.data.decode())["query"]["sql"]
        return _R(json.dumps(
            {"hits": [{"event": "cycle", "verdict": "NG", "_timestamp": 1}]}
        ).encode())

    b = OpenObserveBackend(opener=opener)
    r = b.query("c1", since_us=0, until_us=10**18)
    assert "SELECT *" in captured["sql"]
    assert r.reachable and r.events[0]["verdict"] == "NG"


# ── #7 must_order — sequencing by _timestamp (deterministic via a fake backend) ─
from ooptdd.backends.base import QueryResult  # noqa: E402


class _FixedBackend:
    """Returns a fixed event list with explicit _timestamps — deterministic order."""

    default_lookback_s = 3600
    default_future_buffer_s = 0

    def __init__(self, events):
        self._events = events

    def ship(self, events):  # pragma: no cover - not used
        pass

    def query(self, cid, *, since_us, until_us):
        return QueryResult(reachable=True, events=list(self._events))


def _ev(name, ts):
    return {"event": name, "_timestamp": ts}


def test_must_order_pass_in_sequence():
    b = _FixedBackend([_ev("a", 1), _ev("b", 2), _ev("c", 3)])
    res = evaluate(b, {"cid": "c1", "expect": [{"must_order": ["a", "b", "c"]}]})
    assert res["ok"] and res["checks"][0]["ordered"] and not res["checks"][0]["missing"]


def test_must_order_fail_wrong_sequence():
    b = _FixedBackend([_ev("a", 3), _ev("b", 1)])
    res = evaluate(b, {"cid": "c1", "expect": [{"must_order": ["a", "b"]}]})
    assert not res["ok"] and res["checks"][0]["ordered"] is False


def test_must_order_fail_missing_event():
    b = _FixedBackend([_ev("a", 1)])
    res = evaluate(b, {"cid": "c1", "expect": [{"must_order": ["a", "b"]}]})
    chk = res["checks"][0]
    assert not res["ok"] and chk["missing"] == ["b"] and chk["passed"] is False


def test_must_order_equal_timestamps_allowed():
    # concurrent (same store ts) is non-decreasing → ordered (set/quorum-friendly).
    b = _FixedBackend([_ev("a", 5), _ev("b", 5)])
    res = evaluate(b, {"cid": "c1", "expect": [{"must_order": ["a", "b"]}]})
    assert res["ok"] and res["checks"][0]["ordered"]


def test_must_order_uses_first_occurrence():
    # a repeats after b; ordering keys on the *first* a (1) vs first b (2).
    b = _FixedBackend([_ev("a", 1), _ev("b", 2), _ev("a", 3)])
    res = evaluate(b, {"cid": "c1", "expect": [{"must_order": ["a", "b"]}]})
    assert res["ok"] and res["checks"][0]["firsts"] == {"a": 1, "b": 2}


def test_memory_query_injects_timestamp():
    # locks the backend-contract addition #7 relies on.
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"})
    res = b.query("c1", since_us=0, until_us=10**19)
    assert "_timestamp" in res.events[0] and isinstance(res.events[0]["_timestamp"], int)


# ── #10 optional checks: miss surfaced but not gating; unreachable ≠ clean pass ──
class _UnreachableBackend:
    default_lookback_s = 3600
    default_future_buffer_s = 0

    def ship(self, events):  # pragma: no cover - not used
        pass

    def query(self, cid, *, since_us, until_us):
        return QueryResult(reachable=False, events=[])


def test_optional_miss_does_not_gate_but_is_surfaced():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "req"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"event": "req", "op": ">=", "count": 1},
        {"event": "opt", "op": ">=", "count": 1, "optional": True},
    ]})
    assert res["ok"] is True and res["optional_failed"] == ["opt"]


def test_required_miss_fails_gate():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "x"})
    res = evaluate(b, {"cid": "c1", "expect": [{"event": "req", "op": ">=", "count": 1}]})
    assert res["ok"] is False and res["optional_failed"] == []


def test_unreachable_is_not_ok_even_when_all_optional():
    # infra death (#10 core): store unreachable is never a clean pass, even all-optional.
    res = evaluate(_UnreachableBackend(), {"cid": "c1", "expect": [
        {"event": "opt", "op": ">=", "count": 1, "optional": True},
    ]})
    assert res["ok"] is False and res["reachable"] is False


def test_optional_must_order_miss_surfaced():
    b = _FixedBackend([_ev("a", 1)])  # b missing
    res = evaluate(b, {"cid": "c1", "expect": [
        {"must_order": ["a", "b"], "optional": True},
    ]})
    # all-optional gate asserts nothing GATING -> vacuous, never GREEN (miss still surfaced)
    assert res["ok"] is False and res["vacuous"] is True
    assert res["optional_failed"] == ["must_order:a>b"]


# ── absent / forbid (the negative wing — error logs as failures) ──────────────
def test_absent_passes_when_no_matching_event():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle.final_verdict", "verdict": "PASS"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"present": [{"event": "cycle.final_verdict"}]},
        {"absent": {"where": {"level": "ERROR"}}},
    ]})
    assert res["ok"] is True
    absent_chk = [c for c in res["checks"] if "absent" in c][0]
    assert absent_chk["passed"] is True and absent_chk["violations"] == 0


def test_absent_fails_and_surfaces_offender():
    # a cycle whose good events ALL arrived but which also logged an ERROR =
    # green-and-noisy under positive-only; the forbid wing must turn it RED.
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle.final_verdict", "verdict": "PASS"},
          {"event": "decode", "level": "ERROR", "error": "ZDF decode boom"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"present": [{"event": "cycle.final_verdict"}]},
        {"absent": {"where": {"level": "ERROR"}}},
    ]})
    assert res["ok"] is False
    chk = [c for c in res["checks"] if "absent" in c][0]
    assert chk["passed"] is False and chk["violations"] == 1
    assert "ZDF decode boom" in json.dumps(chk["offending"])


# ── env default: OOPTDD_FORBID_ERRORS injects an implicit error-forbid ─────────
def test_env_forbid_errors_injects_default_gate(monkeypatch):
    monkeypatch.setenv("OOPTDD_FORBID_ERRORS", "1")
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle.final_verdict", "verdict": "PASS"},
          {"event": "render", "level": "CRITICAL", "error": "VTK null"})
    res = evaluate(b, {"cid": "c1", "expect": [{"present": [{"event": "cycle.final_verdict"}]}]})
    assert res["ok"] is False  # no explicit absent rule, yet CRITICAL flips it RED
    assert any("absent" in c for c in res["checks"])


def test_env_forbid_errors_green_when_clean(monkeypatch):
    monkeypatch.setenv("OOPTDD_FORBID_ERRORS", "1")
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle.final_verdict", "verdict": "PASS"})
    res = evaluate(b, {"cid": "c1", "expect": [{"present": [{"event": "cycle.final_verdict"}]}]})
    assert res["ok"] is True


def test_allow_errors_allowlists_a_known_benign_error(monkeypatch):
    monkeypatch.setenv("OOPTDD_FORBID_ERRORS", "1")
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle.final_verdict", "verdict": "PASS"},
          {"event": "zdf.drop", "level": "ERROR", "error": "known benign"})
    res = evaluate(b, {"cid": "c1",
                       "allow_errors": [{"event": "zdf.drop"}],
                       "expect": [{"present": [{"event": "cycle.final_verdict"}]}]})
    assert res["ok"] is True  # the one allowlisted error is exempt


def test_spec_forbid_errors_false_opts_out_of_env_default(monkeypatch):
    monkeypatch.setenv("OOPTDD_FORBID_ERRORS", "1")
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle.final_verdict", "verdict": "PASS"},
          {"event": "x", "level": "ERROR", "error": "tolerated"})
    res = evaluate(b, {"cid": "c1", "forbid_errors": False,
                       "expect": [{"present": [{"event": "cycle.final_verdict"}]}]})
    assert res["ok"] is True  # spec explicitly opted out
