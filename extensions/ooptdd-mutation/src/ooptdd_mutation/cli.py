"""Command-line shell for the optional ``ooptdd-mutation`` distribution.

Run with ``ooptdd-mutation`` or ``python -m ooptdd_mutation.cli``. Importing
:mod:`ooptdd.cli` does not import this module or register these commands.
"""

from __future__ import annotations

import argparse
import json
import sys

from ooptdd.sdk import compose_runtime, load_gate, resolve_gate_policy

from .analysis import (
    mutation_report,
    ranked_kills,
    verify_audit_ranking,
    verify_mutation_lock,
)


def _read_json(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _emit(payload: dict, args, human: str) -> None:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(human, file=sys.stderr)


def _policy(spec: dict):
    runtime = compose_runtime()
    return resolve_gate_policy(spec, runtime.environment, env_keys=runtime.env_keys)


def _lock(args, *, threshold: float | None = None) -> tuple[dict | None, float | None, str | None]:
    if not args.lock:
        return None, threshold, None
    with open(args.spec, "rb") as handle:
        spec_bytes = handle.read()
    lock = _read_json(args.lock)
    resolved, reason = verify_mutation_lock(spec_bytes, lock, threshold)
    info = None
    if reason is None:
        info = {
            "path": args.lock,
            "gate_spec_sha256": lock["gate_spec_sha256"],
            "min_score": resolved,
        }
    return info, resolved, reason


def _mutate(args) -> int:
    spec = load_gate(args.spec)
    lock, threshold, reason = _lock(args, threshold=args.min_score)
    if reason is not None:
        print(f"LOCK REFUSED - {reason}", file=sys.stderr)
        return 2
    report = mutation_report(_read_json(args.events), spec, policy=_policy(spec))
    if lock is not None:
        report["lock"] = lock
    _emit(report, args, f"score={report['score']} n={report['n']}")
    if not report["baseline_green"] or report.get("canary_survived"):
        return 2
    if report["n"] == 0:
        return 2 if threshold is not None else 0
    return 1 if threshold is not None and report["score"] < threshold else 0


def _audit_rank(args) -> int:
    spec = load_gate(args.spec)
    lock, _, reason = _lock(args)
    if reason is not None:
        print(f"LOCK REFUSED - {reason}", file=sys.stderr)
        return 2
    report = _read_json(args.report)
    if args.min_ndcg != args.min_ndcg:
        print("RANKING REFUSED - --min-ndcg is NaN", file=sys.stderr)
        return 2
    if (
        not isinstance(report, dict)
        or not report.get("baseline_green")
        or report.get("canary_survived")
        or report.get("score_status") != "measured"
        or not report.get("mutations")
    ):
        print("RANKING REFUSED - report contains no authenticated measurement", file=sys.stderr)
        return 2
    published = None
    if args.ranking:
        raw = _read_json(args.ranking)
        if not isinstance(raw, list):
            print("RANKING REFUSED - ranking must be a JSON list", file=sys.stderr)
            return 2
        candidates = [item.get("mutation_id") if isinstance(item, dict) else item for item in raw]
        if not all(isinstance(item, str) for item in candidates):
            print("RANKING REFUSED - ranking entries must identify rows", file=sys.stderr)
            return 2
        published = [str(item) for item in candidates]
    result = verify_audit_ranking(
        report,
        spec,
        _read_json(args.events),
        published,
        policy=_policy(spec),
    )
    if not result["ok"]:
        print(f"RANKING REFUSED - {result['reason']}", file=sys.stderr)
        return 2
    payload = {
        "ndcg": result["ndcg"],
        "min_ndcg": args.min_ndcg,
        "n": result["n"],
        "order_sensitive": result["order_sensitive"],
        "ranking_source": "file" if args.ranking else "report-order",
        "ranked": ranked_kills(report),
    }
    if lock is not None:
        payload["lock"] = lock
    _emit(payload, args, f"ndcg={result['ndcg']} n={result['n']}")
    return 1 if result["ndcg"] < args.min_ndcg else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ooptdd-mutation")
    commands = parser.add_subparsers(dest="command", required=True)
    mutate = commands.add_parser("mutate")
    mutate.add_argument("spec")
    mutate.add_argument("--events", required=True)
    mutate.add_argument("--min-score", type=float)
    mutate.add_argument("--lock")
    mutate.add_argument("--json", action="store_true")
    mutate.set_defaults(func=_mutate)
    rank = commands.add_parser("audit-rank")
    rank.add_argument("spec")
    rank.add_argument("--events", required=True)
    rank.add_argument("--report", required=True)
    rank.add_argument("--ranking")
    rank.add_argument("--min-ndcg", type=float, default=1.0)
    rank.add_argument("--lock")
    rank.add_argument("--json", action="store_true")
    rank.set_defaults(func=_audit_rank)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError) as error:
        print(f"ERROR - {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
