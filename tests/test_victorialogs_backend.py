"""VictoriaLogs backend — exercised with an injected opener (no network).

Locks: ship emits JSON-lines to /insert/jsonline with cycle_id as a stream field;
query builds the LogsQL exact-match filter + unix-second window on /select/logsql/query
and parses the JSON-lines response back to whole event rows (so gate where:/counts see
real fields); _time -> _timestamp mapping for must_order; unreachable=False on missing
config; registry resolution of the `victorialogs` name.

From the ooptdd-oss prometheus cycle (A12, seed-ooptdd-backend-victorialogs-20260618).
"""
from __future__ import annotations

import json

from ooptdd.backends import get_backend
from ooptdd.backends.victorialogs import VictoriaLogsBackend, _parse_time_us
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


def test_ship_emits_jsonlines_with_stream_field(monkeypatch):
    monkeypatch.setenv("OOPTDD_VL_URL", "http://vl.test:9428")
    captured: dict = {}

    def opener(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = req.data.decode()
        return _Resp(b"")

    b = VictoriaLogsBackend(opener=opener)
    b.ship([{"event": "cycle", "cycle_id": "c1"},
            {"event": "cycle", "cycle_id": "c1", "verdict": "PASS"}])
    assert "/insert/jsonline" in captured["url"]
    assert "_stream_fields=cycle_id" in captured["url"] and "_msg_field=event" in captured["url"]
    lines = [json.loads(line) for line in captured["body"].splitlines()]
    assert len(lines) == 2 and lines[1]["verdict"] == "PASS"  # one JSON object per line


def test_query_builds_logsql_filter_and_window(monkeypatch):
    monkeypatch.setenv("OOPTDD_VL_URL", "http://vl.test:9428")
    captured: dict = {}

    def opener(req, timeout):
        captured["url"] = req.full_url
        row = {"event": "cycle", "cycle_id": "c1", "verdict": "NG", "_time": "2026-06-18T00:00:00Z"}
        return _Resp((json.dumps(row) + "\n").encode())

    b = VictoriaLogsBackend(opener=opener)
    r = b.query("c1", since_us=1_000_000, until_us=2_000_000)
    assert r.reachable
    assert "/select/logsql/query" in captured["url"]
    # LogsQL exact field match (urlencoded) + unix-second window
    assert "cycle_id" in captured["url"] and "c1" in captured["url"]
    assert "start=1.000000" in captured["url"] and "end=2.000000" in captured["url"]
    ev = r.events[0]
    assert ev["verdict"] == "NG" and ev["event"] == "cycle"
    assert ev["_timestamp"] == _parse_time_us("2026-06-18T00:00:00Z")  # _time -> _timestamp


def test_query_parses_multiple_jsonlines_and_skips_garbage(monkeypatch):
    monkeypatch.setenv("OOPTDD_VL_URL", "http://vl.test:9428")
    body = (
        b'{"event":"a","cycle_id":"c1"}\n'
        b"not-json-should-be-skipped\n"
        b'{"event":"b","cycle_id":"c1"}\n'
    )

    b = VictoriaLogsBackend(opener=lambda req, timeout: _Resp(body))
    r = b.query("c1", since_us=0, until_us=10**18)
    assert r.reachable and [e["event"] for e in r.events] == ["a", "b"]  # garbage row dropped


def test_query_unreachable_when_url_missing(monkeypatch):
    monkeypatch.delenv("OOPTDD_VL_URL", raising=False)
    b = VictoriaLogsBackend()
    assert b.query("c1", since_us=0, until_us=10).reachable is False


def test_gate_evaluates_over_victorialogs_rows(monkeypatch):
    # the whole point: a real gate runs unchanged over rows this driver returns.
    monkeypatch.setenv("OOPTDD_VL_URL", "http://vl.test:9428")
    body = b'{"event":"cycle.final_verdict","cycle_id":"c1","verdict":"PASS"}\n'
    b = VictoriaLogsBackend(opener=lambda req, timeout: _Resp(body))
    res = evaluate(b, {"cid": "c1", "expect": [{"present": [{"event": "cycle.final_verdict"}]}]})
    assert res["ok"] is True


def test_registry_resolves_victorialogs():
    assert isinstance(get_backend("victorialogs"), VictoriaLogsBackend)


def test_parse_time_us_handles_nanoseconds_and_garbage():
    assert _parse_time_us("2026-06-18T00:00:00.123456789Z") is not None  # nanos truncated
    assert _parse_time_us("not-a-time") is None and _parse_time_us(None) is None
