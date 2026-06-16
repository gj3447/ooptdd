import time

from ooptdd.backends import MemoryBackend, get_backend


def _now_window():
    now = int(time.time() * 1_000_000)
    return now - 3_600_000_000, now + 300_000_000


def test_ship_then_query_roundtrip():
    b = MemoryBackend()
    b.ship([{"cid": "c1", "event": "test_session", "total": 2}])
    since, until = _now_window()
    res = b.query("c1", since_us=since, until_us=until)
    assert res.reachable is True
    assert len(res.events) == 1
    assert res.events[0]["event"] == "test_session"


def test_drop_simulates_silent_loss():
    b = MemoryBackend(drop=True)
    b.ship([{"cid": "c2", "event": "test_session"}])
    since, until = _now_window()
    res = b.query("c2", since_us=since, until_us=until)
    assert res.reachable is True  # the store answered...
    assert res.events == []       # ...but nothing was actually stored


def test_get_backend_resolves_builtin():
    assert isinstance(get_backend("memory"), MemoryBackend)


def test_get_backend_unknown_raises():
    import pytest

    with pytest.raises(ValueError):
        get_backend("does-not-exist")
