"""Backend hardening: complete readback (no silent truncation), injection-safe cid binding,
loud ship failures, and the verdict layer refusing a clean pass on an incomplete read.

These pin the two correctness bugs the audit found — OpenObserve's silent ``size:1000`` cap
and cid string-interpolation — plus the cross-cutting completeness contract on the domain
port and the gate.
"""
from __future__ import annotations

import json

import pytest

from ooptdd.backends.base import QueryResult
from ooptdd.backends.clickhouse import ClickHouseBackend
from ooptdd.backends.openobserve import OpenObserveBackend
from ooptdd.backends.victorialogs import VictoriaLogsBackend, _logsql_str
from ooptdd.engine.gate import evaluate


class _Resp:
    def __init__(self, body: bytes = b"", status: int | None = None):
        self._b, self.status = body, status

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── OpenObserve: read paginates to completion, never silently capped ───────────
def _oo_paging_opener(dataset, *, seen):
    def opener(req, timeout):
        q = json.loads(req.data.decode())["query"]
        seen.append(q["from"])
        page = dataset[q["from"]: q["from"] + q["size"]]
        return _Resp(json.dumps({"hits": page}).encode())
    return opener


def test_openobserve_pages_to_completion(monkeypatch):
    monkeypatch.setenv("OOPTDD_OO_URL", "http://oo.test:5080")
    monkeypatch.setenv("OOPTDD_OO_PASSWORD", "x")
    dataset = [{"event": "e", "_timestamp": i} for i in range(2500)]   # 3 pages of 1000
    seen: list[int] = []
    b = OpenObserveBackend(opener=_oo_paging_opener(dataset, seen=seen))
    r = b.query("c1", since_us=0, until_us=10**18)
    assert r.reachable and r.complete
    assert len(r.events) == 2500            # the old size:1000 cap would have lost 1500
    assert seen == [0, 1000, 2000]          # offset advanced page by page


def test_openobserve_runaway_ceiling_surfaces_incomplete(monkeypatch):
    monkeypatch.setenv("OOPTDD_OO_URL", "http://oo.test:5080")
    monkeypatch.setenv("OOPTDD_OO_PASSWORD", "x")
    dataset = [{"event": "e", "_timestamp": i} for i in range(2500)]
    b = OpenObserveBackend(opener=_oo_paging_opener(dataset, seen=[]), max_rows=1500)
    r = b.query("c1", since_us=0, until_us=10**18)
    assert r.complete is False              # ceiling hit -> surfaced, not a silent subset
    assert len(r.events) >= 1500


def test_openobserve_cid_is_escaped_not_interpolated(monkeypatch):
    monkeypatch.setenv("OOPTDD_OO_URL", "http://oo.test:5080")
    monkeypatch.setenv("OOPTDD_OO_PASSWORD", "x")
    captured: dict = {}

    def opener(req, timeout):
        captured["sql"] = json.loads(req.data.decode())["query"]["sql"]
        return _Resp(json.dumps({"hits": []}).encode())

    b = OpenObserveBackend(opener=opener)
    b.query("c1' OR '1'='1", since_us=0, until_us=1)
    # the single quotes are doubled (SQL literal escaping); the injected OR cannot break out
    assert "cycle_id = 'c1'' OR ''1''=''1'" in captured["sql"]


def test_openobserve_ship_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("OOPTDD_OO_URL", "http://oo.test:5080")
    monkeypatch.setenv("OOPTDD_OO_PASSWORD", "x")
    b = OpenObserveBackend(opener=lambda req, timeout: _Resp(status=500))
    with pytest.raises(OSError):
        b.ship([{"cid": "c1", "event": "e"}])   # a dropped ingest is loud, never silent


# ── VictoriaLogs: cid escaped into the LogsQL quoted filter ────────────────────
def test_logsql_escaper_neutralizes_quotes():
    assert _logsql_str('c1"bad') == 'c1\\"bad'
    assert _logsql_str("a\\b") == "a\\\\b"


def test_victorialogs_cid_escaped_in_filter(monkeypatch):
    monkeypatch.setenv("OOPTDD_VL_URL", "http://vl.test:9428")
    captured: dict = {}

    def opener(req, timeout):
        captured["url"] = req.full_url
        return _Resp(b"")

    VictoriaLogsBackend(opener=opener).query('c1"x', since_us=0, until_us=1)
    assert "%5C%22" in captured["url"] or '\\"' in captured["url"]   # escaped, not raw quote


def test_victorialogs_ship_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("OOPTDD_VL_URL", "http://vl.test:9428")
    b = VictoriaLogsBackend(opener=lambda req, timeout: _Resp(status=503))
    with pytest.raises(OSError):
        b.ship([{"cycle_id": "c1", "event": "e"}])


# ── ClickHouse: bounded read surfaces completeness; ship is loud ───────────────
def test_clickhouse_limit_and_completeness(monkeypatch):
    monkeypatch.setenv("OOPTDD_CH_URL", "http://ch.test:8123")
    captured: dict = {}
    # max_rows=2 but 3 rows match -> LIMIT 3 returns 3 -> incomplete
    rows = [{"data": json.dumps({"event": "e", "_timestamp": i})} for i in range(3)]

    def opener(req, timeout):
        captured["url"] = req.full_url
        return _Resp(json.dumps({"data": rows}).encode())

    b = ClickHouseBackend(opener=opener, max_rows=2)
    r = b.query("c1", since_us=0, until_us=1)
    assert "LIMIT+3" in captured["url"] or "LIMIT 3" in captured["url"]
    assert r.complete is False and len(r.events) == 2   # trimmed to max_rows, flagged


def test_clickhouse_ship_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("OOPTDD_CH_URL", "http://ch.test:8123")
    b = ClickHouseBackend(opener=lambda req, timeout: _Resp(status=500))
    with pytest.raises(OSError):
        b.ship([{"cid": "c1", "event": "e"}])


# ── the verdict layer refuses a clean pass on an incomplete read ───────────────
class _IncompleteBackend:
    default_lookback_s = 3600
    default_future_buffer_s = 0

    def ship(self, events):  # pragma: no cover
        pass

    def query(self, cid, *, since_us, until_us):
        # the events WOULD satisfy the gate, but the read was truncated
        return QueryResult(reachable=True, events=[{"event": "a"}], complete=False)


def test_incomplete_read_is_not_a_clean_pass():
    res = evaluate(_IncompleteBackend(), {"cid": "c1", "expect": [
        {"event": "a", "op": ">=", "count": 1},
    ]})
    assert res["complete"] is False
    assert res["ok"] is False          # incomplete evidence can never be GREEN...
    assert res["checks"][0]["passed"] is False   # ...and the check itself is gated by it
