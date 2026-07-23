"""Backend contract consistency (audit 2026-07-08): #4 ClickHouse time window, #5 _seq
tie-break on the network backends.

#4 — ClickHouse `query` accepted since_us/until_us but never put them in the SQL, so a
     reused cid returned stale out-of-window rows (a false green the memory/jsonl drivers
     can't have). Verified by asserting the emitted SQL/params carry the window (no live
     server needed).
#5 — the gap-20 `_seq` tie-break was stamped only by memory/jsonl, so the network drivers
     (openobserve / clickhouse / victorialogs) were tie-blind: two same-`_timestamp` events
     in reversed server order passed a `must_order` gate vacuously. Each driver now stamps
     `_seq` = server return position, so the tie is broken exactly like memory/jsonl.
"""
from __future__ import annotations

import json

from ooptdd.backends.clickhouse import ClickHouseBackend
from ooptdd.backends.openobserve import OpenObserveBackend
from ooptdd.backends.victorialogs import VictoriaLogsBackend
from ooptdd.engine.gate import evaluate


class _Resp:
    def __init__(self, body: bytes):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_MUST_ORDER = {"cid": "c1", "expect": [{"must_order": ["a", "b"]}]}


# ---------------------------------------------------------------- #4 window in SQL
def test_clickhouse_query_bounds_time_window_in_sql(monkeypatch):
    monkeypatch.setenv("OOPTDD_CH_URL", "http://ch.test:8123")
    captured: dict = {}

    def opener(req, timeout):
        captured["url"] = req.full_url
        return _Resp(json.dumps({"data": []}).encode())

    b = ClickHouseBackend(opener=opener)
    b.query("c1", since_us=1000, until_us=9999)
    url = captured["url"]
    # the µs window is the only enforceable guard without a live server
    unquoted = url.replace("%28", "(")
    assert "fromUnixTimestamp64Micro" in url or "fromUnixTimestamp64Micro" in unquoted
    assert "since%3AInt64" in url or "{since:Int64}" in url
    assert "until%3AInt64" in url or "{until:Int64}" in url
    assert "param_since=1000" in url and "param_until=9999" in url
    # regression: the cid parameterization is preserved
    assert "param_cid=c1" in url


# ---------------------------------------------------------------- #5 _seq tie-break
def test_clickhouse_stamps_seq_and_catches_same_ts_inversion(monkeypatch):
    monkeypatch.setenv("OOPTDD_CH_URL", "http://ch.test:8123")

    def opener(req, timeout):
        return _Resp(json.dumps({"data": [
            {"data": json.dumps({"event": "b", "_timestamp": 5_000_000})},
            {"data": json.dumps({"event": "a", "_timestamp": 5_000_000})},
        ]}).encode())

    b = ClickHouseBackend(opener=opener)
    r = b.query("c1", since_us=0, until_us=10**19)
    assert [e["_seq"] for e in r.events] == [0, 1]
    # a must precede b, but b arrived first at an equal timestamp -> ordering violated
    assert evaluate(b, _MUST_ORDER)["ok"] is False


def test_openobserve_stamps_seq_and_catches_same_ts_inversion(monkeypatch):
    monkeypatch.setenv("OOPTDD_OO_URL", "http://oo.test:5080")
    monkeypatch.setenv("OOPTDD_OO_PASSWORD", "x")

    def opener(req, timeout):
        return _Resp(json.dumps({"hits": [
            {"event": "b", "cycle_id": "c1", "_timestamp": 5_000_000},
            {"event": "a", "cycle_id": "c1", "_timestamp": 5_000_000},
        ]}).encode())

    b = OpenObserveBackend(opener=opener)
    r = b.query("c1", since_us=0, until_us=10**19)
    assert [e["_seq"] for e in r.events] == [0, 1]
    assert evaluate(b, _MUST_ORDER)["ok"] is False


def test_victorialogs_stamps_seq_and_catches_same_ts_inversion(monkeypatch):
    monkeypatch.setenv("OOPTDD_VL_URL", "http://vl.test:9428")

    body = (
        b'{"event":"b","cycle_id":"c1","_timestamp":5000000}\n'
        b'{"event":"a","cycle_id":"c1","_timestamp":5000000}\n'
    )
    b = VictoriaLogsBackend(opener=lambda req, timeout: _Resp(body))
    r = b.query("c1", since_us=0, until_us=10**19)
    assert [e["_seq"] for e in r.events] == [0, 1]
    assert evaluate(b, _MUST_ORDER)["ok"] is False
