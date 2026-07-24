#!/usr/bin/env python3
"""Run and emit the deterministic Tier-0 arrival benchmark."""
from __future__ import annotations

import argparse
from pathlib import Path

from ooptdd.benchmark import (
    DEFAULT_FIXTURE_DIR,
    canonical_json,
    render_benchmark_junit,
    render_benchmark_markdown,
    run_tier0_benchmark,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=["0"], default="0")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--inject-fault", choices=["disable-confirm-rounds"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--junit-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    args = parser.parse_args(argv)

    result = run_tier0_benchmark(
        fixture_dir=args.fixture_dir,
        seed=args.seed,
        repetitions=args.repetitions,
        fault_injection=args.inject_fault,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(result), encoding="utf-8")
    if args.junit_out:
        args.junit_out.parent.mkdir(parents=True, exist_ok=True)
        args.junit_out.write_text(render_benchmark_junit(result), encoding="utf-8")
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(render_benchmark_markdown(result), encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
