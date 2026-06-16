"""gate.evaluate — count + `where` field-filter (and, added later, must_order/optional).

Everything is exercised over the in-memory backend (zero infra); the OpenObserve
driver is checked only for whole-row passthrough (the seam #11 needs).
"""
from __future__ import annotations

import json

import pytest

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.gate import evaluate


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
