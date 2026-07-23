#!/usr/bin/env python3
"""Measure the trajectory branch and submit frozen evidence to LakatoTree's pure judge.

The subcommands deliberately separate authorities:

* ``measure`` executes the real ooptdd MemoryBackend ship/query/gate path and the
  platform-score builders.  It emits raw observations, never a LakatoTree verdict.
* ``evidence`` combines independently executed baseline/candidate observations into a
  ``lakato-evidence-record/v1`` record.  The record is rejected if either input is not
  grounded in the declared source commit.
* ``judge`` imports ``lakatos.judge`` from an explicitly pinned LakatoTree checkout and
  derives the verdict.  No caller-provided verdict is accepted.

This is a qualification harness, not production runtime code.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CID = "lakatotree-trajectory-qualification"
GAP_GROUPS = (
    "matcher_composition",
    "forbidden_tool_calls",
    "phoenix_annotation",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT
    ).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _purge_imports() -> None:
    for name in list(sys.modules):
        if name == "ooptdd" or name.startswith("ooptdd."):
            del sys.modules[name]


def _load_ooptdd(source_root: Path):
    source_root = source_root.resolve()
    sys.path.insert(0, str(source_root / "src"))
    _purge_imports()
    importlib.invalidate_caches()
    import ooptdd  # noqa: F401
    from ooptdd import evaluate
    from ooptdd.backends.memory import MemoryBackend, reset
    from ooptdd.integrations.platform_scores import (
        phoenix_annotation_payload,
        post_phoenix_annotations,
    )

    return evaluate, MemoryBackend, reset, phoenix_annotation_payload, post_phoenix_annotations


def _tool(name: str, args: object = None) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event": "gen_ai.execute_tool",
        "gen_ai.tool.name": name,
        "cid": CID,
        "correlation_id": CID,
        "cycle_id": CID,
    }
    if args is not None:
        event["gen_ai.tool.call.arguments"] = args
    return event


def _trajectory_cases() -> list[dict[str, Any]]:
    composed = {"tool_calls": {
        "expected": [{"name": "shell", "args": {"command": {
            "non_empty": True,
            "contains_all": ["git", "status"],
            "not_contains": ["--hard", "rm -rf"],
        }}}],
        "compare": ["name", "args"],
    }}
    forbidden = {"forbidden_tool_calls": [{
        "name": "shell",
        "args": {"command": {"contains_any": ["rm -rf", "git reset --hard"]}},
    }, {
        "name": "write_file",
        "args": {"path": {"contains_any": ["/etc/", "../"]}},
    }]}
    composed_dict = {"tool_calls": {
        "expected": [{"name": "configure", "args": {"options": {
            "has_keys": ["mode"],
            "not_contains": ["secret"],
        }}}],
        "compare": ["name", "args"],
    }}
    cases = [
        # Compatible Phoenix matcher constraints must all hold.
        {"group": "matcher_composition", "name": "safe-dict", "expect_ok": True,
         "rule": composed, "events": [_tool("shell", {"command": "git status --short"})]},
        {"group": "matcher_composition", "name": "safe-json", "expect_ok": True,
         "rule": composed, "events": [_tool("shell", '{"command":"git status --short"}')]},
        {"group": "matcher_composition", "name": "missing-token", "expect_ok": False,
         "unsafe": True, "rule": composed,
         "events": [_tool("shell", {"command": "git diff --short"})]},
        {"group": "matcher_composition", "name": "forbidden-hard", "expect_ok": False,
         "unsafe": True, "rule": composed,
         "events": [_tool("shell", {"command": "git status --hard"})]},
        {"group": "matcher_composition", "name": "forbidden-rm", "expect_ok": False,
         "unsafe": True, "rule": composed,
         "events": [_tool("shell", {"command": "git status && rm -rf build"})]},
        {"group": "matcher_composition", "name": "empty-command", "expect_ok": False,
         "unsafe": True, "rule": composed, "events": [_tool("shell", {"command": ""})]},
        {"group": "matcher_composition", "name": "unparseable-args", "expect_ok": False,
         "unsafe": True, "rule": composed, "events": [_tool("shell", '{"command":')]},
        {"group": "matcher_composition", "name": "dict-composition-safe", "expect_ok": True,
         "rule": composed_dict,
         "events": [_tool("configure", {"options": {"mode": "strict", "color": True}})]},
        {"group": "matcher_composition", "name": "dict-composition-secret", "expect_ok": False,
         "unsafe": True, "rule": composed_dict,
         "events": [_tool("configure", {"options": {"mode": "strict", "secret": "x"}})]},
        {"group": "matcher_composition", "name": "dict-composition-missing-key",
         "expect_ok": False, "unsafe": True, "rule": composed_dict,
         "events": [_tool("configure", {"options": {"color": True}})]},
        # Name + argument prohibition must be narrow and fail closed.
        {"group": "forbidden_tool_calls", "name": "safe-shell", "expect_ok": True,
         "rule": forbidden, "events": [_tool("shell", {"command": "git status --short"})]},
        {"group": "forbidden_tool_calls", "name": "danger-rm", "expect_ok": False,
         "unsafe": True, "rule": forbidden,
         "events": [_tool("shell", {"command": "rm -rf build"})]},
        {"group": "forbidden_tool_calls", "name": "danger-reset-json", "expect_ok": False,
         "unsafe": True, "rule": forbidden,
         "events": [_tool("shell", '{"command":"git reset --hard HEAD~1"}')]},
        {"group": "forbidden_tool_calls", "name": "different-tool", "expect_ok": True,
         "rule": forbidden, "events": [_tool("render", {"command": "rm -rf build"})]},
        {"group": "forbidden_tool_calls", "name": "different-args", "expect_ok": True,
         "rule": forbidden, "events": [_tool("shell", {"path": "build"})]},
        {"group": "forbidden_tool_calls", "name": "unparseable-fails-closed",
         "expect_ok": False, "unsafe": True, "rule": forbidden,
         "events": [_tool("shell", '{"command":')]},
        {"group": "forbidden_tool_calls", "name": "safe-relative-write", "expect_ok": True,
         "rule": forbidden, "events": [_tool("write_file", {"path": "build/out.txt"})]},
        {"group": "forbidden_tool_calls", "name": "etc-write", "expect_ok": False,
         "unsafe": True, "rule": forbidden,
         "events": [_tool("write_file", {"path": "/etc/hosts"})]},
        {"group": "forbidden_tool_calls", "name": "traversal-write", "expect_ok": False,
         "unsafe": True, "rule": forbidden,
         "events": [_tool("write_file", {"path": "../../secrets.txt"})]},
        # Existing DeepEval sequence semantics remain pinned.
        {"group": "sequence_semantics", "name": "subset-extra", "expect_ok": True,
         "rule": {"tool_calls": {"expected": ["plan", "write"], "match": "subset"}},
         "events": [_tool("write"), _tool("noise"), _tool("plan")]},
        {"group": "sequence_semantics", "name": "ordered-swap", "expect_ok": False,
         "unsafe": True,
         "rule": {"tool_calls": {"expected": ["plan", "write"], "match": "ordered"}},
         "events": [_tool("write"), _tool("plan")]},
        {"group": "sequence_semantics", "name": "exact-extra", "expect_ok": False,
         "unsafe": True,
         "rule": {"tool_calls": {"expected": ["plan"], "match": "exact"}},
         "events": [_tool("plan"), _tool("noise")]},
        {"group": "sequence_semantics", "name": "exact-match", "expect_ok": True,
         "rule": {"tool_calls": {"expected": ["plan", "write"], "match": "exact"}},
         "events": [_tool("plan"), _tool("write")]},
    ]
    return cases


def _run_gate_cases(evaluate, MemoryBackend, reset, *, inject_fault: bool) -> list[dict]:
    cases = _trajectory_cases()
    if inject_fault:
        # Same frozen rule, real arrived-event mutation: the previously safe shell call now
        # contains a prohibited command.  The expected label remains GREEN, so the harness
        # itself must turn RED when ooptdd correctly rejects the injected trace.
        for case in cases:
            if case["group"] == "forbidden_tool_calls" and case["name"] == "safe-shell":
                case["events"] = [_tool("shell", {"command": "git status; rm -rf build"})]
                case["injected_fault"] = True
                break
    observations = []
    for case in cases:
        reset()
        backend = MemoryBackend()
        error = None
        observed_ok = None
        readback_count = 0
        try:
            backend.ship(case["events"])
            result = evaluate(backend, {"cid": CID, "expect": [case["rule"]]})
            observed_ok = bool(result["ok"])
            readback_count = int(result.get("scope", {}).get("total", 0))
        except Exception as exc:  # unsupported baseline behavior is evidence, never a pass
            error = f"{type(exc).__name__}: {exc}"
        matched = error is None and observed_ok is case["expect_ok"]
        observations.append({
            "group": case["group"],
            "name": case["name"],
            "expected_ok": case["expect_ok"],
            "observed_ok": observed_ok,
            "matched": matched,
            "unsafe": bool(case.get("unsafe", False)),
            "injected_fault": bool(case.get("injected_fault", False)),
            "readback_count": readback_count,
            "error": error,
        })
        reset()
    return observations


def _platform_cases(payload_builder, poster) -> list[dict]:
    def result(*, ok: bool, reachable: bool = True, complete: bool = True) -> dict:
        return {
            "cid": CID,
            "ok": ok,
            "reachable": reachable,
            "complete": complete,
            "checks": [{"event": "x", "passed": ok}],
            "oracle": {"emit_identity": "memory:lakatotree-qualification"},
        }

    observations: list[dict] = []
    for name, source, label, score in (
        ("present", result(ok=True), "present", 1.0),
        ("absent", result(ok=False), "absent", 0.0),
        ("inconclusive", result(ok=False, reachable=False), "inconclusive", None),
    ):
        error = None
        matched = False
        details: dict[str, Any] = {}
        try:
            annotation = payload_builder(
                source,
                identifier="gate:trajectory-v1",
                metadata={"spec": "qualification-spec.json"},
            )["data"][0]
            got_result = annotation["result"]
            details = {
                "label": got_result.get("label"),
                "score": got_result.get("score"),
                "has_score": "score" in got_result,
                "identifier": annotation.get("identifier"),
                "metadata": annotation.get("metadata"),
            }
            matched = (
                details["label"] == label
                and details["identifier"] == "gate:trajectory-v1"
                and details["metadata"] == {"spec": "qualification-spec.json"}
                and ((score is None and not details["has_score"]) or details["score"] == score)
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        observations.append({
            "group": "phoenix_annotation",
            "name": name,
            "expected_ok": True,
            "observed_ok": matched if error is None else None,
            "matched": matched and error is None,
            "unsafe": label == "absent",
            "error": error,
            "details": details,
        })

    seen: dict[str, Any] = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data)

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Response()

    error = None
    matched = False
    try:
        body = payload_builder(result(ok=True), identifier="gate:trajectory-v1")
        status = poster("http://phoenix.invalid:6006", body, sync=True, opener=opener)
        matched = (
            status == 200
            and seen.get("url", "").endswith("/v1/trace_annotations?sync=true")
            and seen.get("body") == body
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    observations.append({
        "group": "phoenix_annotation",
        "name": "synchronous-idempotent-post",
        "expected_ok": True,
        "observed_ok": matched if error is None else None,
        "matched": matched and error is None,
        "unsafe": False,
        "error": error,
        "details": seen,
    })
    return observations


def measure(source_root: Path, spec_path: Path, *, role: str, inject_fault: bool) -> dict:
    source_root = source_root.resolve()
    (evaluate, MemoryBackend, reset, payload_builder, poster) = _load_ooptdd(source_root)
    observations = _run_gate_cases(
        evaluate, MemoryBackend, reset, inject_fault=inject_fault
    ) + _platform_cases(payload_builder, poster)
    failed_groups = sorted({item["group"] for item in observations if not item["matched"]})
    unresolved = len(set(failed_groups) & set(GAP_GROUPS))
    unsafe = [item for item in observations if item["unsafe"]]
    detected = [item for item in unsafe if item["matched"]]
    detection_rate = len(detected) / len(unsafe) if unsafe else 0.0
    readback = [item for item in observations if "readback_count" in item]
    return {
        "schema_version": "ooptdd-lakatotree-raw-measurement/v1",
        "role": role,
        "measured_at": _utc_now(),
        "source": {
            "root": str(source_root),
            "git_head": _git(source_root, "rev-parse", "HEAD"),
            "dirty": bool(_git(source_root, "status", "--porcelain")),
        },
        "spec": {"path": str(spec_path.resolve()), "sha256": _sha256(spec_path)},
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "pid": os.getpid(),
        },
        "fault_injected": inject_fault,
        "metrics": {
            "unresolved_mechanism_groups": unresolved,
            "unsafe_counterexample_detection_rate": detection_rate,
            "failed_groups": failed_groups,
            "cases_total": len(observations),
            "cases_matched": sum(item["matched"] for item in observations),
            "memory_readback_cases": len(readback),
            "memory_readback_nonempty": sum(item["readback_count"] > 0 for item in readback),
        },
        "observations": observations,
    }


def measure_deepeval(source_root: Path, spec_path: Path) -> dict:
    """Held-out DeepEval process: the bridge must surface safe, dangerous, and corrupt traces."""
    source_root = source_root.resolve()
    _, MemoryBackend, reset, _, _ = _load_ooptdd(source_root)
    from deepeval.test_case import LLMTestCase

    from ooptdd.integrations import make_arrival_metric

    gate = {"cid": CID, "expect": [{"forbidden_tool_calls": [{
        "name": "shell",
        "args": {"command": {"contains_any": ["rm -rf", "git reset --hard"]}},
    }]}]}
    case = LLMTestCase(input="Inspect the repository", actual_output="Done")
    probes = (
        ("safe", {"command": "git status --short"}, 1.0, True),
        ("destructive", {"command": "git reset --hard HEAD~1"}, 0.0, False),
        ("corrupt", '{"command":', 0.0, False),
    )
    observations = []
    for name, arguments, expected_score, expected_success in probes:
        reset()
        backend = MemoryBackend()
        backend.ship([_tool("shell", arguments)])
        metric = make_arrival_metric(gate, backend=backend)
        score = metric.measure(case)
        success = metric.is_successful()
        observations.append({
            "name": name,
            "expected_score": expected_score,
            "observed_score": score,
            "expected_success": expected_success,
            "observed_success": success,
            "matched": score == expected_score and success is expected_success,
            "reason": metric.reason,
            "error": metric.error,
        })
        reset()
    return {
        "schema_version": "ooptdd-deepeval-heldout/v1",
        "measured_at": _utc_now(),
        "source": {
            "root": str(source_root),
            "git_head": _git(source_root, "rev-parse", "HEAD"),
            "dirty": bool(_git(source_root, "status", "--porcelain")),
        },
        "spec": {"path": str(spec_path.resolve()), "sha256": _sha256(spec_path)},
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "deepeval": importlib.import_module("deepeval").__version__,
        },
        "metrics": {
            "actual_deepeval_trajectory_pass_rate": (
                sum(item["matched"] for item in observations) / len(observations)
            ),
            "cases_total": len(observations),
            "cases_matched": sum(item["matched"] for item in observations),
        },
        "observations": observations,
    }


def _contains_key(value: Any, forbidden: str) -> bool:
    if isinstance(value, dict):
        return forbidden in value or any(_contains_key(v, forbidden) for v in value.values())
    if isinstance(value, list):
        return any(_contains_key(v, forbidden) for v in value)
    return False


def build_evidence(prereg_path: Path, candidate_path: Path, baseline_path: Path,
                   positive_path: Path, negative_path: Path, novel_path: Path) -> dict:
    prereg = json.loads(prereg_path.read_text())
    candidate = json.loads(candidate_path.read_text())
    baseline = json.loads(baseline_path.read_text())
    negative = json.loads(negative_path.read_text())
    positive = json.loads(positive_path.read_text())
    novel = json.loads(novel_path.read_text())
    if candidate != positive:
        raise ValueError("candidate and positive measurement must be the same frozen artifact")
    if candidate["fault_injected"] or baseline["fault_injected"] or not negative["fault_injected"]:
        raise ValueError("positive/baseline/negative fault modes do not match the contract")
    metric = candidate["metrics"]
    return {
        "schema_version": "lakato-evidence-record/v1",
        "programme": prereg["programme"],
        "branch": prereg["branch"],
        "conjecture": prereg["conjecture"],
        "measurement": {
            "metric_name": "unresolved_mechanism_groups",
            "value": metric["unresolved_mechanism_groups"],
            "unit": "count",
            "scope": "three preregistered deterministic trajectory/platform gaps",
            "novel_metric_name": "actual_deepeval_trajectory_pass_rate",
            "novel_value": novel["metrics"]["actual_deepeval_trajectory_pass_rate"],
        },
        "comparison": {
            "baseline_git_head": baseline["source"]["git_head"],
            "baseline_value": baseline["metrics"]["unresolved_mechanism_groups"],
            "candidate_git_head": candidate["source"]["git_head"],
            "candidate_value": metric["unresolved_mechanism_groups"],
            "baseline_failed_groups": baseline["metrics"]["failed_groups"],
            "candidate_failed_groups": metric["failed_groups"],
        },
        "negative_oracle": {
            "technique": "inject a destructive shell call into a preregistered safe case",
            "same_spec_sha256": (
                negative["spec"]["sha256"] == candidate["spec"]["sha256"]
            ),
            "failed_groups": negative["metrics"]["failed_groups"],
            "restored_by_positive_replay": candidate["metrics"]["failed_groups"] == [],
        },
        "grounded": True,
        "provenance": {
            "preregistration": {"path": str(prereg_path), "sha256": _sha256(prereg_path)},
            "candidate": {"path": str(candidate_path), "sha256": _sha256(candidate_path)},
            "baseline": {"path": str(baseline_path), "sha256": _sha256(baseline_path)},
            "negative": {"path": str(negative_path), "sha256": _sha256(negative_path)},
            "novel": {"path": str(novel_path), "sha256": _sha256(novel_path)},
            "harness": {
                "path": str(Path(__file__).resolve()),
                "sha256": _sha256(Path(__file__).resolve()),
                "command": "measure -> evidence",
                "environment": candidate["environment"],
            },
        },
        "findings": [
            "The candidate must close all three preregistered groups.",
            "The frozen baseline must expose the same three gaps for a claimed delta.",
            "The controlled destructive-call injection must break the same frozen contract.",
            "MemoryBackend evidence is same-process arrival readback, not external corroboration.",
        ],
    }


def run_judge(lakatotree_root: Path, prereg_path: Path, evidence_path: Path) -> dict:
    prereg = json.loads(prereg_path.read_text())
    evidence = json.loads(evidence_path.read_text())
    if _contains_key(evidence, "verdict"):
        raise ValueError("evidence record must not contain a hand-entered verdict")
    if evidence["provenance"]["preregistration"]["sha256"] != _sha256(prereg_path):
        raise ValueError("evidence does not bind the supplied preregistration")
    registered = datetime.fromisoformat(prereg["registered_at"])
    measured = datetime.fromisoformat(
        json.loads(Path(evidence["provenance"]["candidate"]["path"]).read_text())["measured_at"]
    )
    if registered >= measured:
        raise ValueError("preregistration was not locked before measurement")
    lakatotree_root = lakatotree_root.resolve()
    sys.path.insert(0, str(lakatotree_root))
    from lakatos.verdict.judge import NovelTarget, Prediction, judge

    prediction = prereg["prediction"]
    novel = prereg["novel_target"]
    primary_value = float(evidence["measurement"]["value"])
    novel_value = float(evidence["measurement"]["novel_value"])
    computed = judge(
        Prediction(
            metric_name=prediction["metric"],
            direction=prediction["direction"],
            baseline_value=float(prediction["baseline"]),
            noise_band=float(prediction["noise_band"]),
            novel_prediction=prereg["conjecture"],
            scale_type=prediction.get("scale_type", "ratio"),
        ),
        primary_value,
        novel_target=NovelTarget(
            metric_name=novel["metric"],
            direction=novel["direction"],
            threshold=float(novel["threshold"]),
        ),
        novel_measured=novel_value,
        measured_sha=evidence["provenance"]["candidate"]["sha256"],
        novel_sha=evidence["provenance"]["novel"]["sha256"],
        require_independent_source=True,
    )
    kill_reasons = []
    comparison = evidence["comparison"]
    if primary_value > 0:
        kill_reasons.append("candidate left one or more mechanism groups unresolved")
    if novel_value < float(novel["threshold"]):
        kill_reasons.append("unsafe counterexample detection missed the threshold")
    if set(comparison["baseline_failed_groups"]) & set(GAP_GROUPS) != set(GAP_GROUPS):
        kill_reasons.append("baseline did not expose all three claimed gaps")
    if not evidence["negative_oracle"]["same_spec_sha256"]:
        kill_reasons.append("negative used a different specification")
    if not evidence["negative_oracle"]["failed_groups"]:
        kill_reasons.append("fault injection did not break the qualification harness")
    if not evidence["negative_oracle"]["restored_by_positive_replay"]:
        kill_reasons.append("positive replay did not restore the qualification state")
    return {
        "schema_version": "ooptdd-lakatotree-judge-response/v1",
        "computed_at": _utc_now(),
        "programme": prereg["programme"],
        "branch": prereg["branch"],
        "conjecture": prereg["conjecture"],
        "lakato_result": {
            "verdict": computed.verdict,
            "delta": computed.delta,
            "improved": computed.improved,
            "novel": computed.novel,
            "reason": computed.reason,
        },
        "kill_condition_triggered": bool(kill_reasons),
        "kill_reasons": kill_reasons,
        "source": {
            "root": str(lakatotree_root),
            "git_head": _git(lakatotree_root, "rev-parse", "HEAD"),
            "entrypoint": "lakatos.verdict.judge:judge",
        },
        "bindings": {
            "preregistration_sha256": _sha256(prereg_path),
            "evidence_sha256": _sha256(evidence_path),
            "judge_script_sha256": _sha256(Path(__file__).resolve()),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    measure_parser = sub.add_parser("measure")
    measure_parser.add_argument("--source-root", type=Path, required=True)
    measure_parser.add_argument("--spec", type=Path, required=True)
    measure_parser.add_argument("--role", choices=("baseline", "candidate"), required=True)
    measure_parser.add_argument("--inject-fault", action="store_true")
    measure_parser.add_argument("--output", type=Path, required=True)

    deepeval_parser = sub.add_parser("deepeval-probe")
    deepeval_parser.add_argument("--source-root", type=Path, required=True)
    deepeval_parser.add_argument("--spec", type=Path, required=True)
    deepeval_parser.add_argument("--output", type=Path, required=True)

    evidence_parser = sub.add_parser("evidence")
    evidence_parser.add_argument("--prereg", type=Path, required=True)
    evidence_parser.add_argument("--candidate", type=Path, required=True)
    evidence_parser.add_argument("--baseline", type=Path, required=True)
    evidence_parser.add_argument("--positive", type=Path, required=True)
    evidence_parser.add_argument("--negative", type=Path, required=True)
    evidence_parser.add_argument("--novel", type=Path, required=True)
    evidence_parser.add_argument("--output", type=Path, required=True)

    judge_parser = sub.add_parser("judge")
    judge_parser.add_argument("--lakatotree-root", type=Path, required=True)
    judge_parser.add_argument("--prereg", type=Path, required=True)
    judge_parser.add_argument("--evidence", type=Path, required=True)
    judge_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "measure":
        output = measure(
            args.source_root, args.spec, role=args.role, inject_fault=args.inject_fault
        )
    elif args.command == "deepeval-probe":
        output = measure_deepeval(args.source_root, args.spec)
    elif args.command == "evidence":
        output = build_evidence(
            args.prereg, args.candidate, args.baseline, args.positive, args.negative,
            args.novel,
        )
    else:
        output = run_judge(args.lakatotree_root, args.prereg, args.evidence)
    _write_json(args.output, output)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
