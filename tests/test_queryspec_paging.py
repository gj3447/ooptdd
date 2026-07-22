"""QuerySpec ``limit``/``cursor`` activation — OpenObserve reference implementation.

The reserved fields stop being speculative: ``OpenObserveBackend.query_spec`` honors
a bounded page (``limit`` + opaque offset ``cursor``), returns ``next_cursor``, and
the generic :func:`fetch_all_pages` walks cursors to completion. The ENGINE's live
read path is untouched: a spec with neither limit nor cursor delegates to the
existing read-to-completion ``query()`` byte-identically (pinned here).
"""
from __future__ import annotations

import json

import pytest

from ooptdd.backends.openobserve import OpenObserveBackend
from ooptdd.domain.ports import QueryResult, QuerySpec, TimeWindow, fetch, fetch_all_pages


class PagedOpener:
    """Serves `total` rows in whatever from/size the request asks for."""

    def __init__(self, total):
        self.total = total
        self.requests = []

    def __call__(self, req, timeout):
        body = json.loads(req.data)
        q = body["query"]
        self.requests.append(q)
        start, size = q.get("from", 0), q["size"]
        hits = [{"cycle_id": "c", "event": f"e{i}", "_timestamp": 1_000 + i}
                for i in range(start, min(start + size, self.total))]
        payload = json.dumps({"hits": hits}).encode()

        class R:
            status = 200

            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()


@pytest.fixture
def oo(monkeypatch):
    monkeypatch.setenv("OOPTDD_OO_URL", "http://oo:5080")
    monkeypatch.setenv("OOPTDD_OO_PASSWORD", "pw")

    def make(total, **kw):
        opener = PagedOpener(total)
        return OpenObserveBackend(stream="s", org="o", opener=opener, **kw), opener
    return make


def _spec(**kw):
    return QuerySpec(cid="c", window=TimeWindow(0, 10_000_000), **kw)


def test_engine_live_path_delegates_to_full_read(oo):
    backend, opener = oo(total=7)
    res = fetch(backend, _spec())  # no limit/cursor — the live path
    assert res.reachable and len(res.events) == 7 and res.complete
    assert res.next_cursor is None


def test_bounded_page_returns_next_cursor(oo):
    backend, opener = oo(total=7)
    page1 = backend.query_spec(_spec(limit=3))
    assert [e["event"] for e in page1.events] == ["e0", "e1", "e2"]
    assert page1.next_cursor == "3" and page1.complete is False
    page2 = backend.query_spec(_spec(limit=3, cursor=page1.next_cursor))
    assert [e["event"] for e in page2.events] == ["e3", "e4", "e5"]
    assert opener.requests[-1]["from"] == 3
    page3 = backend.query_spec(_spec(limit=3, cursor=page2.next_cursor))
    assert [e["event"] for e in page3.events] == ["e6"]
    assert page3.next_cursor is None and page3.complete is True


def test_page_seq_positions_are_global_not_per_page(oo):
    backend, _ = oo(total=5)
    page2 = backend.query_spec(_spec(limit=2, cursor="2"))
    assert [e["_seq"] for e in page2.events] == [2, 3]


def test_bad_cursor_is_a_loud_usage_error(oo):
    backend, _ = oo(total=1)
    with pytest.raises(ValueError):
        backend.query_spec(_spec(limit=2, cursor="page-two"))


def test_fetch_all_pages_walks_to_completion(oo):
    backend, opener = oo(total=10)
    res = fetch_all_pages(backend, _spec(limit=4))
    assert len(res.events) == 10 and res.complete is True
    assert res.next_cursor is None
    assert [e["_seq"] for e in res.events] == list(range(10))
    assert len(opener.requests) == 3  # 4 + 4 + 2


def test_fetch_all_pages_max_rows_guard_is_honest(oo):
    backend, _ = oo(total=10)
    res = fetch_all_pages(backend, _spec(limit=4), max_rows=8)
    assert len(res.events) == 8
    assert res.complete is False and res.next_cursor is not None


def test_fetch_all_pages_requires_a_query_spec_backend():
    class Legacy:
        def query(self, cid, *, since_us, until_us):
            return QueryResult(reachable=True)

    with pytest.raises(TypeError):
        fetch_all_pages(Legacy(), _spec(limit=2))
