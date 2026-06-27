"""JSONL backend TDD — conformance(전체 port 계약) + cross-process + reachable/complete 정직.

# KG: ooptdd-jsonl-queryable-backend-2026-06-27
"""
from __future__ import annotations

import pytest

from ooptdd.backends.conformance import assert_backend_conforms
from ooptdd.backends.jsonl import JsonlBackend


def test_jsonl_backend_conforms(tmp_path):
    # 전체 Backend 계약(round-trip / whole-row / _timestamp / 완전성 / injection-safe cid).
    p = str(tmp_path / "events.jsonl")
    assert_backend_conforms(lambda: JsonlBackend(path=p))


def test_jsonl_cross_process_readback(tmp_path):
    # 다른 인스턴스(=다른 프로세스 흉내)가 ship 한 걸 읽는다 — MemoryBackend 가 못 하던 것.
    p = str(tmp_path / "x.jsonl")
    JsonlBackend(path=p).ship([{"cid": "c1", "event": "a", "k": "v"}])
    r = JsonlBackend(path=p).query("c1", since_us=0, until_us=10**19)
    assert r.reachable and any(e["event"] == "a" and e["k"] == "v" for e in r.events)
    assert all("_timestamp" in e for e in r.events)


def test_jsonl_missing_file_is_absent_not_unreachable(tmp_path):
    # 아직 ship 안 됨: store 는 도달 가능(absent ⊥), inconclusive(?) 가 아니다.
    r = JsonlBackend(path=str(tmp_path / "none.jsonl")).query("c", since_us=0, until_us=10**19)
    assert r.reachable is True and r.events == []


def test_jsonl_window_filters_out_of_range(tmp_path):
    p = str(tmp_path / "w.jsonl")
    b = JsonlBackend(path=p)
    b.ship([{"cid": "c", "event": "e"}])
    # 미래 창(아직 안 옴) → 빈 결과, 그래도 reachable.
    r = b.query("c", since_us=10**18, until_us=10**19)
    assert r.reachable is True and r.events == []


def test_jsonl_registered_in_registry(tmp_path):
    from ooptdd.backends import get_backend

    b = get_backend("jsonl", path=str(tmp_path / "r.jsonl"))
    assert isinstance(b, JsonlBackend) and b.queryable is True


def test_jsonl_requires_path(monkeypatch):
    monkeypatch.delenv("OOPTDD_JSONL_PATH", raising=False)
    with pytest.raises(ValueError):
        JsonlBackend()
