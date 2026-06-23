"""The backend conformance kit validates the reference backend and is not green-and-blind:
a driver that drops events, loses fields, or fails to bind the cid safely must fail it.
"""
from __future__ import annotations

import pytest

from ooptdd.backends.base import QueryResult
from ooptdd.backends.conformance import (
    assert_backend_conforms,
    assert_writeonly_backend_conforms,
)
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


# ── write-only (no read side) conformance: verified via a capture sink ──────────────


class _Capture:
    """Stands in for a store-side reader the driver itself lacks (e.g. an OTLP
    InMemoryLogExporter adapter): records exactly what the driver shipped."""
    def __init__(self):
        self.records = []

    def export(self, events):
        self.records.extend(events)


class _WriteOnlyBackend:
    """A conformant write-only driver (like OTLP): ships to a capture sink, no read side."""
    default_lookback_s = 0
    default_future_buffer_s = 0
    queryable = False

    def __init__(self, capture):
        self._cap = capture

    def ship(self, events):
        self._cap.export(events)

    def query(self, cid, *, since_us, until_us):
        return QueryResult(reachable=False)  # honest: write-only -> inconclusive, never absent


class _DroppingWriteOnlyBackend(_WriteOnlyBackend):
    """Broken: silently drops on export, so the capture sink sees nothing."""
    def ship(self, events):
        pass


class _LyingWriteOnlyBackend(_WriteOnlyBackend):
    """Broken: claims a read side it does not have (reachable=True, empty) — a silent absent,
    the false green the kit must catch (strict over it would wrongly fail or pass)."""
    def query(self, cid, *, since_us, until_us):
        return QueryResult(reachable=True)


def _wo_harness(cls):
    def make():
        cap = _Capture()
        return cls(cap), cap
    return make


def test_writeonly_backend_conforms():
    # the reference write-only shape passes the contract it *can* honour
    assert_writeonly_backend_conforms(_wo_harness(_WriteOnlyBackend))


def test_writeonly_kit_catches_a_dropping_exporter():
    with pytest.raises(AssertionError):
        assert_writeonly_backend_conforms(_wo_harness(_DroppingWriteOnlyBackend))


def test_writeonly_kit_catches_a_lying_read_side():
    # a write-only driver whose query() reports reachable=True is a silent-absent liar
    with pytest.raises(AssertionError):
        assert_writeonly_backend_conforms(_wo_harness(_LyingWriteOnlyBackend))
