"""ABSENT — the founding incident: a silent 401, and "shipped OK" is a lie.

The 2026 incident: an ingest credential went stale; the fire-and-forget shipper
swallowed the 401 (as fire-and-forget code always does) and the app logged
"shipped OK" for 22 hours while every event was dropped. Every return-value
test stayed green.

This script reproduces it: `legacy_ship` is that fire-and-forget wrapper. The
app's self-report says OK; the independent verifier reads the store back and
says ABSENT. Exits 0 only if the lie was caught.

Needs a running OpenObserve + valid OOPTDD_OO_* env vars (the verifier uses the
good credentials; only the shipper's are broken — exactly the incident shape).
"""
from __future__ import annotations

import os
import sys
import uuid

from ooptdd import get_backend
from ooptdd.engine.verify import verify_gate

cid = f"demo-401-{uuid.uuid4().hex[:8]}"

# The shipper with the stale credential (env-only secrets, so: a wrong-password env).
os.environ["DEMO_STALE_PASSWORD"] = "stale-credential-from-last-quarter"
broken_shipper = get_backend("openobserve", stream="demo",
                             password_env="DEMO_STALE_PASSWORD")


def legacy_ship(events) -> str:
    """Fire-and-forget, as found in the wild: any ship error is swallowed."""
    try:
        broken_shipper.ship(events)
    except Exception:
        pass  # <- the 22 hours live here
    return "shipped OK"


status = legacy_ship([
    {"event": "order.shipped", "cid": cid, "correlation_id": cid, "cycle_id": cid},
])
print(f"[app] {status} (cid={cid})  <- the self-report. It is green. It is wrong.")

# The verifier holds its own (valid) credentials and asks the store, not the app.
verifier_backend = get_backend("openobserve", stream="demo")
gate = {"cid": cid, "expect": [{"event": "order.shipped", "op": "gte", "target": 1}]}
res = verify_gate(verifier_backend, cid, gate, retries=3, delay=1.0)
print(f"[verifier] verdict={res['verdict']} reasons={res.get('reasons')}")
assert res["verdict"] == "absent", f"expected ABSENT, got {res['verdict']}"
print("ABSENT — the store is the judge: 'shipped OK' lied, and the gate caught it "
      "in seconds, not 22 hours.")
sys.exit(0)
