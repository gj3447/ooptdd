"""ClickHouse backend (Tier-2 #6) — exercised with an injected opener (no network).

Locks: parameterized injection-safe cid, SELECT * whole-row passthrough, `data`
envelope unwrap (so gate `where`/counts see real fields), unreachable=False on
missing config, and registry resolution incl. the `signoz` alias.
"""
from __future__ import annotations

import json

from ooptdd.backends import get_backend
from ooptdd.backends.clickhouse import ClickHouseBackend
from ooptdd.gate import evaluate


class _Resp:
    def __init__(self, body: bytes):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_query_is_parameterized_and_unwraps_data(monkeypatch):
    monkeypatch.setenv("OOPTDD_CH_URL", "http://ch.test:8123")
    captured: dict = {}

    def opener(req, timeout):
        captured["url"] = req.full_url
        # the envelope is carried in `data` as a JSON string (our ship format)
        return _Resp(json.dumps({"data": [
            {"cycle_id": "c1", "event": "cycle",
             "data": json.dumps({"event": "cycle", "verdict": "NG", "_timestamp": 7})},
        ]}).encode())

    b = ClickHouseBackend(opener=opener)
    r = b.query("c1", since_us=0, until_us=10**18)
    assert r.reachable
    assert "{cid:String}" in captured["url"] or "%7Bcid%3AString%7D" in captured["url"]
    assert "param_cid=c1" in captured["url"]
    ev = r.events[0]
    assert ev["verdict"] == "NG" and ev["event"] == "cycle" and ev["_timestamp"] == 7


def test_ship_emits_jsoneachrow(monkeypatch):
    monkeypatch.setenv("OOPTDD_CH_URL", "http://ch.test:8123")
    captured: dict = {}

    def opener(req, timeout):
        captured["body"] = req.data.decode()
        return _Resp(b"")

    b = ClickHouseBackend(opener=opener)
    b.ship([{"event": "cycle", "cid": "c1", "verdict": "PASS"}])
    assert captured["body"].startswith("INSERT INTO tests FORMAT JSONEachRow\n")
    row = json.loads(captured["body"].splitlines()[1])
    assert row["cycle_id"] == "c1" and row["event"] == "cycle"
    assert json.loads(row["data"])["verdict"] == "PASS"


def test_missing_url_is_inconclusive_not_crash(monkeypatch):
    monkeypatch.delenv("OOPTDD_CH_URL", raising=False)
    r = ClickHouseBackend().query("c1", since_us=0, until_us=1)
    assert r.reachable is False and r.events == []


def test_network_error_is_inconclusive(monkeypatch):
    monkeypatch.setenv("OOPTDD_CH_URL", "http://ch.test:8123")

    def opener(req, timeout):
        raise OSError("connection refused")

    r = ClickHouseBackend(opener=opener).query("c1", since_us=0, until_us=1)
    assert r.reachable is False


def test_gate_runs_over_clickhouse_rows(monkeypatch):
    # end-to-end: a gate evaluates against unwrapped ClickHouse rows like any backend.
    monkeypatch.setenv("OOPTDD_CH_URL", "http://ch.test:8123")

    def opener(req, timeout):
        return _Resp(json.dumps({"data": [
            {"data": json.dumps({"event": "cycle", "verdict": "NG", "_timestamp": 1})},
            {"data": json.dumps({"event": "cycle", "verdict": "PASS", "_timestamp": 2})},
        ]}).encode())

    b = ClickHouseBackend(opener=opener)
    res = evaluate(b, {"cid": "c1", "expect": [
        {"event": "cycle", "where": {"verdict": "NG"}, "op": "eq", "target": 1},
    ]})
    assert res["ok"] and res["checks"][0]["got"] == 1


def test_registry_resolves_clickhouse_and_signoz():
    assert isinstance(get_backend("clickhouse"), ClickHouseBackend)
    assert isinstance(get_backend("signoz"), ClickHouseBackend)  # SigNoz alias
