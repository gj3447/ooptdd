"""Backend abstraction — the portability seam.

A backend does exactly two things:

    ship(events)   write structured event envelopes to the store
    query(cid, ..) read them back, returning whether the query itself was
                   reachable plus the matching events

Keeping the surface this small is deliberate. The interesting logic — the
LTL3 verdict (present / absent / inconclusive), the polling window, the
clock-skew buffer — lives in :mod:`ooptdd.verify`, *above* the backend, so it is
identical no matter where events land. A backend never decides pass/fail; it
only fetches.

`reachable` is the key field: it distinguishes "the store says no such event"
(absent / ⊥) from "I couldn't even ask the store" (inconclusive / ?). Conflating
those two is how a network blip becomes a flaky test.

Drivers are discovered three ways, in order:
  1. built-ins (memory, openobserve, otel)
  2. the ``ooptdd.backends`` entry-point group (``pip install`` a 3rd-party driver)
  3. an explicit instance passed in code
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class QueryResult:
    """Outcome of a single backend query.

    reachable: True iff the query round-trip succeeded (regardless of hits).
               False means the store was unreachable -> verdict stays `?`.
    events:    the matching event envelopes (dicts), newest-or-any order.
    """

    reachable: bool
    events: list[dict] = field(default_factory=list)


@runtime_checkable
class Backend(Protocol):
    """Minimal driver contract. Implement these two methods; that's a backend."""

    #: Per-backend polling hints (seconds). Stores with slow ingest override these.
    default_lookback_s: int
    default_future_buffer_s: int
    #: False iff the backend has no read side (e.g. OTLP/otel is write-only). The verify
    #: layer cannot confirm arrival on such a backend, so `strict` over it is meaningless —
    #: callers must surface that loudly rather than passing silently. Defaults True (most
    #: backends can read); callers read it via ``getattr(backend, "queryable", True)``.
    queryable: bool = True

    def ship(self, events: list[dict]) -> None:
        """Write events. Must be best-effort; raising is allowed but the caller
        treats a ship failure as a warning, never a build failure."""
        ...

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        """Return events carrying ``cid`` whose store timestamp falls in the
        microsecond window ``[since_us, until_us]``."""
        ...
