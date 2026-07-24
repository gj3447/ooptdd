#!/usr/bin/env python3
"""Fail-closed validator for trajectory and DeepEval raw measurement artifacts.

This is deliberately separate from the frozen v2 qualification harness: changing that
harness would invalidate its historical hash chain.  New CI invokes this validator on
fresh artifacts and treats its exit status as the gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ooptdd.evidence_integrity import (
    EvidenceIntegrityError,
    sha256_file,
    validate_deepeval_measurement,
)


def validate_deepeval(
    artifact: Path,
    *,
    source_head: str,
    spec_path: Path,
    expected_version: str,
) -> dict:
    record = json.loads(artifact.read_text(encoding="utf-8"))
    return validate_deepeval_measurement(
        record,
        expected_head=source_head,
        expected_spec_sha256=sha256_file(spec_path),
        expected_version=expected_version,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    deep = sub.add_parser("deepeval")
    deep.add_argument("--artifact", type=Path, required=True)
    deep.add_argument("--source-head", required=True)
    deep.add_argument("--spec", type=Path, required=True)
    deep.add_argument("--expected-version", default="4.0.7")
    args = parser.parse_args(argv)
    try:
        if args.command == "deepeval":
            result = validate_deepeval(
                args.artifact,
                source_head=args.source_head,
                spec_path=args.spec,
                expected_version=args.expected_version,
            )
        else:  # pragma: no cover - argparse enforces the command
            raise AssertionError(args.command)
    except (OSError, json.JSONDecodeError, EvidenceIntegrityError) as exc:
        print(f"INVALID_EVIDENCE: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
