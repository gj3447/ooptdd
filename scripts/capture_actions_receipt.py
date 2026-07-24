#!/usr/bin/env python3
"""Capture a normalized ooptdd Actions receipt from GitHub's live public API."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_ooptdd_efficacy_evidence import _live_ci_receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.run_id <= 0:
        raise SystemExit("run-id must be positive")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(
            _live_ci_receipt(args.run_id),
            stream,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        stream.write("\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
