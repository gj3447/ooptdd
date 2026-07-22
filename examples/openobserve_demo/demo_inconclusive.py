"""INCONCLUSIVE — the third value: a down store is not a failure verdict.

Points the verifier at a dead endpoint. The verdict must be INCONCLUSIVE (?),
never ABSENT (⊥): demoting "couldn't observe" to "falsified" is how an infra
blip becomes a flaky RED — and how teams learn to ignore their gates.

No OpenObserve needed (that's the point). Exits 0 only if the verdict was
honest about its own blindness.
"""
from __future__ import annotations

import os
import sys
import uuid

from ooptdd import get_backend
from ooptdd.engine.verify import verify_gate

cid = f"demo-infra-{uuid.uuid4().hex[:8]}"

os.environ["DEMO_DEAD_URL"] = "http://127.0.0.1:59999"  # nothing listens here
os.environ.setdefault("OOPTDD_OO_PASSWORD", "irrelevant-store-is-down")
backend = get_backend("openobserve", stream="demo", url_env="DEMO_DEAD_URL", timeout=2.0)

gate = {"cid": cid, "expect": [{"event": "order.shipped", "op": "gte", "target": 1}]}
res = verify_gate(backend, cid, gate, retries=2, delay=0.5)
print(f"[verifier] verdict={res['verdict']} reasons={res.get('reasons')}")
assert res["verdict"] == "inconclusive", f"expected INCONCLUSIVE, got {res['verdict']}"
print("INCONCLUSIVE — the verifier reports its own blindness instead of inventing "
      "a RED. Exit code 2 in CI: hold, don't fail.")
sys.exit(0)
