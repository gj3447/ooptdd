"""Tier-1 gate vocabulary: OpenSLO/Keptn alignment + `present` (unordered subset).

All additive — the existing symbolic-op / `count` specs in test_gate.py still pass.
These lock the borrowed surface: word operators, `target`, `timeWindow`, the
SLI/SLO split (`indicators`/`indicatorRef`), `ratioMetric`, and `present`.
"""
from __future__ import annotations

import pytest

from ooptdd.backends.base import QueryResult
from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.engine.gate import duration_s, evaluate


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


def _ship(backend, cid, *events):
    backend.ship([{"cid": cid, **e} for e in events])


# ── word operators + target (OpenSLO objective vocabulary) ────────────────────
def test_word_operator_and_target_alias():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "test_outcome"}, {"event": "test_outcome"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"event": "test_outcome", "op": "gte", "target": 2},
    ]})
    assert res["ok"] and res["checks"][0]["op"] == ">=" and res["checks"][0]["want"] == 2


def test_word_operator_eq_zero():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle", "verdict": "PASS"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"event": "cycle", "where": {"verdict": "NG"}, "op": "eq", "target": 0},
    ]})
    assert res["ok"]


# ── SLI/SLO split: indicators (how to select) referenced by indicatorRef ──────
def test_indicator_ref_decouples_query_from_criteria():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle", "verdict": "NG"}, {"event": "cycle", "verdict": "PASS"})
    res = evaluate(b, {
        "cid": "c1",
        "indicators": {"ng": {"event": "cycle", "where": {"verdict": "NG"}}},
        "expect": [{"indicatorRef": "ng", "op": "eq", "target": 1}],
    })
    assert res["ok"] and res["checks"][0]["got"] == 1


def test_indicator_ref_inline_where_extends_base():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle", "verdict": "NG", "station": "A"},
          {"event": "cycle", "verdict": "NG", "station": "B"})
    res = evaluate(b, {
        "cid": "c1",
        "indicators": {"ng": {"event": "cycle", "where": {"verdict": "NG"}}},
        "expect": [{"indicatorRef": "ng", "where": {"station": "A"}, "op": "eq", "target": 1}],
    })
    assert res["ok"] and res["checks"][0]["got"] == 1


# ── ratioMetric (good/total) ──────────────────────────────────────────────────
def test_ratio_metric_pass():
    b = MemoryBackend()
    _ship(b, "c1", *([{"event": "cycle", "verdict": "PASS"}] * 99
                     + [{"event": "cycle", "verdict": "NG"}]))
    res = evaluate(b, {
        "cid": "c1",
        "indicators": {"done": {"event": "cycle", "where": {"verdict": "PASS"}}},
        "expect": [{"ratioMetric": {"good": {"indicatorRef": "done"},
                                    "total": {"event": "cycle"}}, "op": "gte", "target": 0.99}],
    })
    chk = res["checks"][0]
    assert res["ok"] and chk["good"] == 99 and chk["total"] == 100 and chk["value"] == 0.99


def test_ratio_metric_fail_below_target():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "cycle", "verdict": "PASS"}, {"event": "cycle", "verdict": "NG"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"ratioMetric": {"good": {"event": "cycle", "where": {"verdict": "PASS"}},
                         "total": {"event": "cycle"}}, "op": "gte", "target": 0.99},
    ]})
    assert not res["ok"] and res["checks"][0]["value"] == 0.5


def test_ratio_metric_zero_total_is_not_a_pass():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "other"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"ratioMetric": {"good": {"event": "cycle"}, "total": {"event": "cycle"}},
         "op": "gte", "target": 0.99},
    ]})
    assert not res["ok"] and res["checks"][0]["reason"] == "ratio_total_zero"


# ── present: subset, any order (testfixtures order_matters=False) ──────────────
def test_present_passes_regardless_of_order():
    b = MemoryBackend()
    # ship b before a — present must not care about order
    _ship(b, "c1", {"event": "b", "station": "A"}, {"event": "a"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"present": [{"event": "a"}, {"event": "b", "where": {"station": "A"}}]},
    ]})
    assert res["ok"] and res["checks"][0]["missing"] == []


def test_present_surfaces_missing_matcher():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"present": [{"event": "a"}, {"event": "b"}]},
    ]})
    assert not res["ok"] and res["checks"][0]["missing"] == ["b"]


def test_present_optional_miss_does_not_gate():
    b = MemoryBackend()
    _ship(b, "c1", {"event": "a"})
    res = evaluate(b, {"cid": "c1", "expect": [
        {"present": [{"event": "a"}, {"event": "b"}], "optional": True},
    ]})
    # all-optional gate is vacuous (asserts nothing gating) -> not GREEN; miss still surfaced
    assert res["ok"] is False and res["vacuous"] is True
    assert res["optional_failed"] == ["present:a,b"]


# ── timeWindow parsing (OpenSLO rolling window) ───────────────────────────────
@pytest.mark.parametrize("raw,want", [
    ("30s", 30), ("5m", 300), ("2h", 7200), ("1d", 86400), (45, 45), ("90", 90), (None, None),
])
def test_duration_parsing(raw, want):
    assert duration_s(raw) == want


def test_time_window_drives_lookback(monkeypatch):
    captured = {}

    class _B:
        default_lookback_s = 3600
        default_future_buffer_s = 0

        def ship(self, events):  # pragma: no cover
            pass

        def query(self, cid, *, since_us, until_us):
            captured["span_s"] = (until_us - since_us) // 1_000_000
            return QueryResult(reachable=True, events=[{"event": "a"}])

    evaluate(_B(), {"cid": "c1", "timeWindow": "5m", "expect": [{"event": "a"}]})
    assert captured["span_s"] == 300


# ── unreachable still never a clean pass for the new kinds ────────────────────
class _Unreachable:
    default_lookback_s = 3600
    default_future_buffer_s = 0

    def ship(self, events):  # pragma: no cover
        pass

    def query(self, cid, *, since_us, until_us):
        return QueryResult(reachable=False, events=[])


def test_present_unreachable_is_not_ok():
    res = evaluate(_Unreachable(), {"cid": "c1", "expect": [{"present": [{"event": "a"}]}]})
    assert res["ok"] is False and res["reachable"] is False
