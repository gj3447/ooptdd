"""Backend conformance kit — one shared contract every Backend driver must satisfy.

``pip install``-ing a third-party driver (registered under the ``ooptdd.backends`` entry
point) is only safe if the driver actually honours the port's contract: ship-then-query
round-trips, whole rows come back (so gate ``where:`` filters see real fields), each event
carries a ``_timestamp``, the correlation id is bound safely (a cid with quotes/specials
round-trips and never breaks or injects), and a normal read reports ``complete``. This module
gives driver authors a single function to assert all of that against a store they can write
to — and ooptdd self-tests it against the reference :class:`~ooptdd.backends.memory.MemoryBackend`.

Usage (in a driver's own test suite)::

    from ooptdd.backends.conformance import assert_backend_conforms
    def test_my_driver_conforms():
        assert_backend_conforms(lambda: MyBackend(...))   # raises AssertionError on any gap

It lives in the adapter layer (it drives a concrete backend and runs the engine's gate over
the result), so it never widens the engine's dependency surface.
"""
from __future__ import annotations

from collections.abc import Callable

from ..engine.gate import evaluate

#: A factory returning a *fresh, write-and-read* backend bound to whatever store the author
#: wants exercised (the in-memory default, or a real OpenObserve/ClickHouse/… via env/opener).
BackendFactory = Callable[[], object]


def assert_backend_conforms(make_backend: BackendFactory, *, cid: str = "ooptdd-conf-cid") -> None:
    """Assert the backend ``make_backend()`` honours the Backend contract. Raises
    ``AssertionError`` on the first violation, naming what failed.

    Covers: ship→query round-trip, whole-row (``where:``) passthrough, ``_timestamp``
    passthrough, injection-safe cid binding, and the ``complete`` completeness flag. The
    backend must actually persist and read back (the memory backend, or a real store); a
    write-only driver (``queryable=False``) is out of scope for read conformance.
    """
    backend = make_backend()
    # 1. ship → query round-trip: every shipped event for the cid comes back.
    events = [
        {"cid": cid, "event": "alpha", "verdict": "PASS", "n": 1},
        {"cid": cid, "event": "alpha", "verdict": "NG", "n": 2},
        {"cid": cid, "event": "beta", "verdict": "PASS", "n": 3},
    ]
    backend.ship(events)
    res = backend.query(cid, since_us=0, until_us=10**19)
    assert res.reachable, "query of a just-shipped cid must be reachable"
    got = res.events
    assert len(got) >= 3, f"round-trip lost events: shipped 3, read {len(got)}"

    # 2. whole-row passthrough: arbitrary fields survive so gate `where:` can filter on them.
    alphas_ng = [e for e in got if e.get("event") == "alpha" and e.get("verdict") == "NG"]
    assert len(alphas_ng) == 1, "whole-row passthrough lost the `verdict` field"

    # 3. _timestamp passthrough: ordering checks need a store-receive timestamp.
    assert all("_timestamp" in e for e in got), "every returned event must carry _timestamp"

    # 4. completeness: a normal, in-window read reports complete (a full answer).
    assert getattr(res, "complete", True) is True, "a complete read must report complete=True"

    # 5. the gate runs over the rows like any backend (the whole portability point).
    gate = evaluate(backend, {"cid": cid, "expect": [
        {"event": "alpha", "where": {"verdict": "NG"}, "op": "==", "count": 1},
        {"present": [{"event": "beta"}]},
    ]})
    assert gate["ok"], f"gate over conformant rows should pass, got {gate}"

    # 6. injection-safe cid binding: a cid with quotes/specials round-trips, never breaks.
    nasty = "conf'\"; OR 1=1 --:cid"
    b2 = make_backend()
    b2.ship([{"cid": nasty, "event": "safe", "marker": "yes"}])
    r2 = b2.query(nasty, since_us=0, until_us=10**19)
    assert r2.reachable, "a cid with special characters must still query cleanly"
    assert any(e.get("marker") == "yes" for e in r2.events), \
        "a cid with quotes/specials must round-trip (cid binding must be escaped/parameterized)"
