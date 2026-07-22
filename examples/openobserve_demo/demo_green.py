"""PRESENT — the happy path: emit, then positively confirm arrival.

Needs a running OpenObserve (see README.md / docker-compose.yml) and the
OOPTDD_OO_* env vars. Exits 0 only if arrival was CONFIRMED by readback.
"""
from __future__ import annotations

import sys
import uuid

from ooptdd import get_backend
from ooptdd.engine.verify import verify_gate

cid = f"demo-green-{uuid.uuid4().hex[:8]}"
backend = get_backend("openobserve", stream="demo")

backend.ship([
    {"event": "order.received", "cid": cid, "correlation_id": cid, "cycle_id": cid},
    {"event": "order.shipped", "cid": cid, "correlation_id": cid, "cycle_id": cid},
])
print(f"[app] shipped 2 events (cid={cid})")

gate = {"cid": cid, "expect": [
    {"event": "order.received", "op": "gte", "target": 1},
    {"event": "order.shipped", "op": "gte", "target": 1},
]}
res = verify_gate(backend, cid, gate, retries=6, delay=1.0)
print(f"[verifier] verdict={res['verdict']} reasons={res.get('reasons')}")
assert res["verdict"] == "present", f"expected PRESENT, got {res['verdict']}"
print("PRESENT — arrival confirmed by independent readback. Proof, not trust.")
sys.exit(0)
