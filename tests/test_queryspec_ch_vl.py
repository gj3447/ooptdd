"""ClickHouse / VictoriaLogs typed read surfaces — the honest per-store shape.

Neither store's API is cursor-native, so the drivers do NOT pretend otherwise:

- ClickHouse: no cursor primitive, but ``LIMIT n OFFSET k`` is exact and stable
  under the driver's fixed ordering → an opaque decimal-offset cursor is
  synthesized (same shape as the OpenObserve one).
- VictoriaLogs: LogsQL streams every match with no paging primitive at all, so
  the opt-in is **limit-only** — a cursor is refused loudly rather than faked,
  and a filled limit reports ``complete=False`` (there may be more, unknowably).

In both, a spec with neither limit nor cursor delegates to the existing
read-to-completion ``query()`` — the engine's live path is unchanged.
"""
from __future__ import annotations

import json

import pytest

from ooptdd.backends.clickhouse import ClickHouseBackend
from ooptdd.backends.victorialogs import VictoriaLogsBackend
from ooptdd.domain.ports import QuerySpec, TimeWindow, fetch_all_pages


def _spec(**kw):
    return QuerySpec(cid="c", window=TimeWindow(0, 10_000_000), **kw)


def _resp(payload: bytes):
    class R:
        status = 200

        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return R()


# ── ClickHouse: offset-emulated cursor ─────────────────────────────────────────
class ChOpener:
    def __init__(self, total):
        self.total, self.queries = total, []

    def __call__(self, params, body, headers):
        sql = params["query"]
        self.queries.append(sql)
        limit = int(sql.split("LIMIT ")[1].split()[0])
        offset = int(sql.split("OFFSET ")[1].split()[0]) if "OFFSET " in sql else 0
        rows = [{"data": json.dumps({"event": f"e{i}"}), "_timestamp": 1000 + i}
                for i in range(offset, min(offset + limit, self.total))]
        return _resp(json.dumps({"data": rows}).encode())


@pytest.fixture
def ch(monkeypatch):
    monkeypatch.setenv("OOPTDD_CH_URL", "http://ch:8123")

    def make(total):
        opener = ChOpener(total)
        backend = ClickHouseBackend(table="tests")
        backend._post = lambda params, body, headers: opener(params, body, headers)
        return backend, opener
    return make


def test_ch_live_path_has_no_offset_clause(ch):
    backend, opener = ch(total=3)
    res = backend.query_spec(_spec())
    assert res.reachable and len(res.events) == 3 and res.next_cursor is None
    assert "OFFSET" not in opener.queries[0]  # untouched read-to-completion SQL


def test_ch_bounded_page_emits_offset_and_cursor(ch):
    backend, opener = ch(total=5)
    page1 = backend.query_spec(_spec(limit=2))
    assert [e["event"] for e in page1.events] == ["e0", "e1"]
    assert page1.next_cursor == "2" and page1.complete is False
    page2 = backend.query_spec(_spec(limit=2, cursor="2"))
    assert [e["event"] for e in page2.events] == ["e2", "e3"]
    assert "OFFSET 2" in opener.queries[-1]
    last = backend.query_spec(_spec(limit=2, cursor="4"))
    assert [e["event"] for e in last.events] == ["e4"]
    assert last.next_cursor is None and last.complete is True


def test_ch_seq_is_the_global_position(ch):
    backend, _ = ch(total=4)
    page = backend.query_spec(_spec(limit=2, cursor="2"))
    assert [e["_seq"] for e in page.events] == [2, 3]


def test_ch_bad_cursor_is_loud(ch):
    backend, _ = ch(total=1)
    with pytest.raises(ValueError):
        backend.query_spec(_spec(limit=1, cursor="not-a-number"))


def test_ch_walks_with_fetch_all_pages(ch):
    backend, opener = ch(total=7)
    res = fetch_all_pages(backend, _spec(limit=3))
    assert [e["event"] for e in res.events] == [f"e{i}" for i in range(7)]
    assert res.complete is True and len(opener.queries) == 3


# ── VictoriaLogs: limit-only, cursor refused ───────────────────────────────────
class VlOpener:
    def __init__(self, total):
        self.total, self.urls = total, []

    def __call__(self, req, timeout):
        self.urls.append(req.full_url)
        limit = self.total
        if "limit+" in req.full_url or "limit%20" in req.full_url:
            tail = req.full_url.replace("%20", "+").split("limit+")[1]
            limit = int(tail.split("&")[0])
        lines = "\n".join(json.dumps({"event": f"e{i}", "_time": "2026-07-23T00:00:00Z"})
                          for i in range(min(limit, self.total)))
        return _resp(lines.encode())


@pytest.fixture
def vl(monkeypatch):
    monkeypatch.setenv("OOPTDD_VL_URL", "http://vl:9428")

    def make(total):
        opener = VlOpener(total)
        return VictoriaLogsBackend(opener=opener), opener
    return make


def test_vl_live_path_has_no_limit_pipe(vl):
    backend, opener = vl(total=3)
    res = backend.query_spec(_spec())
    assert res.reachable and len(res.events) == 3
    assert "limit" not in opener.urls[0]


def test_vl_limit_only_is_honored_and_honest_about_completeness(vl):
    backend, opener = vl(total=10)
    res = backend.query_spec(_spec(limit=4))
    assert len(res.events) == 4
    assert "limit" in opener.urls[-1]
    # a FILLED limit cannot prove the set is exhausted — LogsQL has no cursor to ask with
    assert res.complete is False and res.next_cursor is None


def test_vl_short_read_under_the_limit_is_complete(vl):
    backend, _ = vl(total=2)
    res = backend.query_spec(_spec(limit=5))
    assert len(res.events) == 2 and res.complete is True


def test_vl_cursor_is_refused_not_faked(vl):
    backend, _ = vl(total=3)
    with pytest.raises(ValueError) as exc:
        backend.query_spec(_spec(limit=2, cursor="2"))
    assert "cursor" in str(exc.value).lower()


def test_vl_is_not_walkable_by_fetch_all_pages(vl):
    # fetch_all_pages needs next_cursor to advance; VL never emits one, so a bounded
    # walk terminates after one page instead of looping forever — surfaced honestly.
    backend, _ = vl(total=10)
    res = fetch_all_pages(backend, _spec(limit=4))
    assert len(res.events) == 4 and res.complete is False
