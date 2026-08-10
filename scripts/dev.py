#!/usr/bin/env python3
"""Two-tier local validation entry point: a fast inner loop and a completion gate.

`check` is meant to run after every edit. `verify` is meant to run once, before
claiming a task is done. Both mirror steps that already exist in
`.github/workflows/ci.yml`; this module exists so that a contributor or a coding
agent has one command to run instead of transcribing YAML.

`verify` is deliberately a strict subset of CI: the cross-OS/cross-Python
matrix, the installed-wheel extension jobs, the DeepEval and LakatoTree
qualification jobs, and the Docker-backed OpenObserve demos cannot run here. The
subset is reported on every run so a local green is never mistaken for a CI
green.

Stdlib only, matching the other checkers in this directory.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Prefix that puts commands in the locked environment, exactly as CI does.
RUNNER = ("uv", "run", "--no-sync")

LINT_TARGETS = (
    "src/",
    "tests/",
    "examples/",
    "extensions/",
    "scripts/check_functional_architecture.py",
    "scripts/check_ooptdd_ouroboros_readiness.py",
    "scripts/dev.py",
)

EXAMPLE_TESTS = (
    "examples/test_order_pipeline.py",
    "examples/test_agent_trajectory.py",
    "examples/integrations/test_otel_verdict_export.py",
)

Stage = tuple[str, tuple[str, ...]]

CHECK_STAGES: tuple[Stage, ...] = (
    ("architecture contract", ("python", "scripts/check_functional_architecture.py")),
    ("ouroboros readiness", ("python", "scripts/check_ooptdd_ouroboros_readiness.py")),
    ("lint", ("ruff", "check", *LINT_TARGETS)),
    ("package typing", ("mypy", "src/ooptdd")),
    ("strict ouroboros typing", ("mypy", "--strict", "src/ooptdd/ouroboros")),
)

VERIFY_ONLY_STAGES: tuple[Stage, ...] = (
    ("test suite", ("pytest", "-q")),
    ("ship-once invariant (xdist)", ("pytest", "-q", "-n", "2")),
    ("adoption examples", ("pytest", "-q", *EXAMPLE_TESTS)),
    ("locked trajectory receipt", ("python", "scripts/verify_agent_trajectory_receipt.py")),
    ("backend matrix current", ("python", "scripts/gen_backend_matrix.py", "--check")),
)

NOT_COVERED_LOCALLY = (
    "cross-OS / cross-Python test matrix",
    "installed-wheel extension distributions",
    "DeepEval integration",
    "LakatoTree qualification replay",
    "OpenObserve demo trio and live parity wing (needs Docker)",
)


def stages_for(command: str) -> tuple[Stage, ...]:
    if command == "check":
        return CHECK_STAGES
    return CHECK_STAGES + VERIFY_ONLY_STAGES


def run_stage(name: str, argv: tuple[str, ...]) -> int:
    printable = " ".join(RUNNER + argv)
    print(f"\n=== {name}\n$ {printable}", flush=True)
    return subprocess.call(RUNNER + argv, cwd=REPO_ROOT)


def main(raw_args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command",
        nargs="?",
        default="check",
        choices=("check", "verify"),
        help="check = fast inner loop; verify = completion gate (default: check)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the stages without running them",
    )
    args = parser.parse_args(raw_args)

    selected = stages_for(args.command)

    if args.list:
        print(f"{args.command} runs {len(selected)} stage(s):")
        for name, argv in selected:
            print(f"  {name}\n    $ {' '.join(RUNNER + argv)}")
        if args.command == "verify":
            print("\nnot covered locally (CI only):")
            for item in NOT_COVERED_LOCALLY:
                print(f"  - {item}")
        return 0

    if shutil.which(RUNNER[0]) is None:
        print(
            f"error: {RUNNER[0]!r} is not on PATH. Install uv, then run\n"
            "  uv sync --locked --extra dev --extra otel",
            file=sys.stderr,
        )
        return 127

    for name, argv in selected:
        code = run_stage(name, argv)
        if code != 0:
            print(
                f"\nFAILED at stage {name!r} (exit {code}). "
                "Remaining stages were not run.",
                file=sys.stderr,
            )
            return code

    print(f"\n{args.command}: all {len(selected)} stage(s) passed.")
    if args.command == "verify":
        print("This is a subset of CI. Not covered locally:")
        for item in NOT_COVERED_LOCALLY:
            print(f"  - {item}")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    raise SystemExit(main())
