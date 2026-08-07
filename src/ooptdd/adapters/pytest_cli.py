"""Explicit command-line shell for the optional pytest adapter."""

from __future__ import annotations

import argparse
import json
import sys

from ..bootstrap import compose_runtime
from .pytest import verify_trace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ooptdd.adapters.pytest_cli")
    parser.add_argument("cid")
    parser.add_argument("--expect-total", type=int)
    parser.add_argument("--backend")
    parser.add_argument("--retries", type=int)
    parser.add_argument("--delay", type=float)
    parser.add_argument("--backoff", type=float)
    parser.add_argument("--max-delay", dest="max_delay", type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    overrides = {
        name: value
        for name in ("backend", "retries", "delay", "backoff", "max_delay")
        if (value := getattr(args, name)) is not None
    }
    runtime = compose_runtime(overrides=overrides)
    result = verify_trace(
        runtime.backend(),
        args.cid,
        expect_total=args.expect_total,
        polling=runtime.settings.polling,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["ok"]:
        return 0
    if result["verdict"] == "inconclusive":
        print("INCONCLUSIVE - store unreachable", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
