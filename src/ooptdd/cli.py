"""``ooptdd`` command line — manual verification and gate evaluation.

    ooptdd verify <cid> [--backend memory] [--expect-total N] [--retries R]
    ooptdd gate <spec.yaml> [--backend memory]
    ooptdd version

Settings come from ``[tool.ooptdd]`` in the working-directory ``pyproject.toml``,
overridden by environment variables and the flags below. Exit 0 = GREEN
(arrival confirmed / gate passed), 1 = RED, 2 = inconclusive (store unreachable).
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .backends import get_backend
from .config import from_mapping, load_pyproject
from .gate import evaluate, load_gate
from .verify import verify_trace


def _settings(args):
    s = from_mapping(load_pyproject())
    if getattr(args, "backend", None):
        s.backend = args.backend
    return s


def _cmd_verify(args) -> int:
    s = _settings(args)
    backend = get_backend(s.backend, service=s.service, **s.backend_options)
    res = verify_trace(
        backend,
        args.cid,
        expect_total=args.expect_total,
        retries=args.retries,
        delay=args.delay,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    verdict = res["verdict"]
    if res["ok"]:
        print("GREEN — arrival confirmed", file=sys.stderr)
        return 0
    if verdict == "inconclusive":
        print(f"INCONCLUSIVE — store unreachable: {res['reasons']}", file=sys.stderr)
        return 2
    print(f"RED — positive assertion failed: {res['reasons']}", file=sys.stderr)
    return 1


def _cmd_gate(args) -> int:
    s = _settings(args)
    backend = get_backend(s.backend, service=s.service, **s.backend_options)
    spec = load_gate(args.spec)
    res = evaluate(backend, spec)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    # surface optional misses distinctly — they don't change the exit code but must be
    # visible (a silently-degraded optional stream should never read as "all clean").
    if res.get("optional_failed"):
        print(f"WARN — optional checks failed (not gating): {res['optional_failed']}",
              file=sys.stderr)
    if res["ok"]:
        print(f"GREEN — gate passed (cid={res['cid']})", file=sys.stderr)
        return 0
    if not res["reachable"]:
        print("INCONCLUSIVE — store unreachable", file=sys.stderr)
        return 2
    print("RED — gate failed", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ooptdd", description="logs-as-spec test verification")
    p.add_argument("--version", action="version", version=f"ooptdd {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="assert a cid's test_session trace arrived")
    v.add_argument("cid")
    v.add_argument("--backend")
    v.add_argument("--expect-total", type=int)
    v.add_argument("--retries", type=int, default=4)
    v.add_argument("--delay", type=float, default=1.0)
    v.set_defaults(func=_cmd_verify)

    g = sub.add_parser("gate", help="evaluate a YAML gate spec")
    g.add_argument("spec")
    g.add_argument("--backend")
    g.set_defaults(func=_cmd_gate)

    sub.add_parser("version", help="print version").set_defaults(
        func=lambda _a: (print(__version__) or 0)
    )

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
