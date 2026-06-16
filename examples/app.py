"""A toy 'order pipeline' that emits structured trace events as it works.

This stands in for any real system (a microservice, an LLM agent loop, a data
job). The point of ooptdd is that the test asserts on *these emitted events*,
read back from a store — not on a return value the code could fake.
"""
from __future__ import annotations

from ooptdd.backends import Backend


def process_order(backend: Backend, cid: str, *, items: int) -> dict:
    """Process an order, emitting one event per step under correlation id ``cid``."""
    def ev(event, **attrs):
        return {"cid": cid, "correlation_id": cid, "cycle_id": cid,
                "service": "shop.orders", "event": event, **attrs}

    backend.ship([ev("order_received", items=items)])
    backend.ship([ev("payment_authorized")])
    for i in range(items):
        backend.ship([ev("line_item_packed", index=i)])
    backend.ship([ev("order_shipped")])
    # The function returns "ok" no matter what the store did — that's exactly the
    # lie ooptdd refuses to trust.
    return {"status": "ok", "items": items}
