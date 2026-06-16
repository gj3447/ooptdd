"""In-process backend — zero network, zero infra, deterministic.

This is the default backend and the single biggest lever for adoption: you can
run the whole ooptdd loop (ship -> verify -> verdict) in a plain `pytest` with
nothing installed and nothing running. The demo, the plugin's own test suite,
and most users' first green all ride on this.

Events live in a module-global store so that a `ship` in one place and a
`verify` in another (e.g. the pytest session hook) see the same data within a
process. `reachable` is always True — there is no network to fail — so the only
verdicts you can get are `present` or `absent`, never `inconclusive`. That makes
it perfect for reproducing the silent-ingest-loss bug on purpose.
"""
from __future__ import annotations

import time

from .base import QueryResult

# process-global store: cid -> list[(stored_us, event)]
_STORE: dict[str, list[tuple[int, dict]]] = {}


def reset() -> None:
    """Clear the store (handy between tests)."""
    _STORE.clear()


class MemoryBackend:
    """A fake store that keeps events in a dict. Drop-in for CI and demos."""

    default_lookback_s = 3600
    default_future_buffer_s = 0

    def __init__(self, *, drop: bool = False, **_ignored):
        # ``drop=True`` silently discards everything shipped — this is how the
        # killer demo simulates a backend that accepts then loses your events.
        self.drop = drop

    def ship(self, events: list[dict]) -> None:
        if self.drop or not events:
            return
        now_us = int(time.time() * 1_000_000)
        for ev in events:
            cid = ev.get("cid") or ev.get("correlation_id") or ev.get("cycle_id") or ""
            _STORE.setdefault(cid, []).append((now_us, ev))

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        hits = [
            ev for (ts, ev) in _STORE.get(cid, []) if since_us <= ts <= until_us
        ]
        return QueryResult(reachable=True, events=hits)
