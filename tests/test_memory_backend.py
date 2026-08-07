import time

from ooptdd.backends import MemoryBackend, MemoryStore, get_backend
from ooptdd.backends.memory import reset


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
    assert res.events == ()  # ...but nothing was actually stored


def test_get_backend_resolves_builtin():
    assert isinstance(get_backend("memory"), MemoryBackend)


def test_get_backend_unknown_raises():
    import pytest

    with pytest.raises(ValueError):
        get_backend("does-not-exist")


def test_default_stores_are_instance_local():
    writer = MemoryBackend()
    reader = MemoryBackend()
    writer.ship([{"cid": "isolated", "event": "value"}])
    since, until = _now_window()

    assert writer.query("isolated", since_us=since, until_us=until).events
    assert reader.query("isolated", since_us=since, until_us=until).events == ()


def test_explicit_store_enables_deliberate_sharing_and_targeted_reset():
    store = MemoryStore()
    writer = MemoryBackend(store=store)
    reader = MemoryBackend(store=store)
    writer.ship([{"cid": "shared", "event": "value"}])
    since, until = _now_window()

    assert reader.query("shared", since_us=since, until_us=until).events
    reset(store)
    assert writer.query("shared", since_us=since, until_us=until).events == ()


def test_legacy_targetless_reset_is_an_isolated_noop():
    first = MemoryBackend()
    second = MemoryBackend()
    first.ship([{"cid": "first", "event": "value"}])

    reset()
    since, until = _now_window()

    assert first.query("first", since_us=since, until_us=until).events
    assert second.query("first", since_us=since, until_us=until).events == ()


def test_ship_snapshots_caller_owned_event_values():
    backend = MemoryBackend()
    event = {"cid": "snapshot", "event": "value", "payload": {"n": 1}}
    backend.ship([event])
    event["payload"]["n"] = 99
    since, until = _now_window()

    result = backend.query("snapshot", since_us=since, until_us=until)

    assert result.events[0]["payload"]["n"] == 1


def test_domain_specific_cycle_id_is_not_a_backend_query_key():
    backend = MemoryBackend()
    backend.ship([{"cycle_id": "domain-cycle", "event": "value"}])
    since, until = _now_window()

    assert backend.query("domain-cycle", since_us=since, until_us=until).events == ()
