#!/usr/bin/env python3
"""Reproduce the locked GREEN -> injected RED -> restored GREEN trajectory receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

from ooptdd import evaluate, evidence_tier
from ooptdd.backends.memory import MemoryBackend, reset

DEFAULT_SPEC = Path("docs/receipts/agent-trajectory-gate-2026-07-23.yaml")


def _run(spec: dict, command: str) -> dict:
    reset()
    backend = MemoryBackend()
    cid = str(spec["cid"])
    backend.ship([{
        "event": "gen_ai.execute_tool",
        "gen_ai.tool.name": "shell",
        "gen_ai.tool.call.arguments": {"command": command},
        "cid": cid,
        "correlation_id": cid,
        "cycle_id": cid,
    }])
    result = evaluate(backend, spec)
    result["evidence_tier"] = evidence_tier(result)
    return result


def verify(spec_path: Path) -> dict:
    spec_bytes = spec_path.read_bytes()
    spec = yaml.safe_load(spec_bytes)
    positive = _run(spec, "git status --short")
    negative = _run(spec, "rm -rf build")
    restored = _run(spec, "git status --short")
    if not positive["ok"] or negative["ok"] or not restored["ok"]:
        raise AssertionError("locked trajectory gate did not reproduce GREEN -> RED -> GREEN")
    if positive["scope"]["charge_ratio"] <= 0:
        raise AssertionError("positive wing was uncharged")
    return {
        "spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
        "positive": positive,
        "negative": negative,
        "restored": restored,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    args = parser.parse_args()
    print(json.dumps(verify(args.spec), ensure_ascii=False, indent=2, sort_keys=True))
    reset()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
