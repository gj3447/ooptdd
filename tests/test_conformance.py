"""The backend conformance kit validates the reference backend and is not green-and-blind:
a driver that drops events, loses fields, or fails to bind the cid safely must fail it.
"""
from __future__ import annotations

import pytest

from ooptdd.backends.base import QueryResult
from ooptdd.backends.conformance import assert_backend_conforms
from ooptdd.backends.memory import MemoryBackend, reset


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


def test_memory_backend_conforms():
    # the reference implementation passes the whole contract
    assert_backend_conforms(MemoryBackend)


class _DropsFieldsBackend:
    """A broken driver: round-trips event names but loses arbitrary fields and timestamps."""
    default_lookback_s = 3600
    default_future_buffer_s = 0

    def __init__(self):
        self._store = []

    def ship(self, events):
        self._store.extend(events)

    def query(self, cid, *, since_us, until_us):
        # strips everything but the event name -> where-passthrough + _timestamp lost
        return QueryResult(reachable=True,
                           events=[{"event": e.get("event")} for e in self._store
                                   if e.get("cid") == cid])


class _SilentCapBackend:
    """A broken driver: silently returns only the first row and lies that it is complete."""
    default_lookback_s = 3600
    default_future_buffer_s = 0

    def __init__(self):
        self._store = []

    def ship(self, events):
        self._store.extend(events)

    def query(self, cid, *, since_us, until_us):
        rows = [{**e, "_timestamp": 1} for e in self._store if e.get("cid") == cid]
        return QueryResult(reachable=True, events=rows[:1], complete=True)  # silent truncation


def test_kit_catches_a_field_dropping_backend():
    with pytest.raises(AssertionError):
        assert_backend_conforms(_DropsFieldsBackend)


def test_kit_catches_a_silently_truncating_backend():
    with pytest.raises(AssertionError):
        assert_backend_conforms(_SilentCapBackend)
