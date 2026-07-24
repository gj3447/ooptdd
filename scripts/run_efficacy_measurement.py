#!/usr/bin/env python3
"""Run the prospectively locked Tier-0 positive/negative/restored sequence."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from run_lakatotree_trajectory_judge import measure_deepeval

from ooptdd.benchmark import (
    DEFAULT_FIXTURE_DIR,
    canonical_json,
    render_benchmark_junit,
    render_benchmark_markdown,
    run_tier0_benchmark,
    tier0_provenance,
    validate_tier0_result,
)
from ooptdd.evidence_integrity import (
    EvidenceIntegrityError,
    expected_deepeval_mismatch,
    measurement_environment,
    prospective_git_receipt,
    validate_deepeval_measurement,
    validate_measurement_lock,
    validate_registration_repository,
)

SCHEMA = "ooptdd-efficacy-measurement-sequence/v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(source_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source_root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _write_result(output_dir: Path, stem: str, result: dict) -> dict:
    json_path = output_dir / f"{stem}.json"
    junit_path = output_dir / f"{stem}.xml"
    markdown_path = output_dir / f"{stem}.md"
    json_path.write_text(canonical_json(result), encoding="utf-8")
    junit_path.write_text(render_benchmark_junit(result), encoding="utf-8")
    markdown_path.write_text(render_benchmark_markdown(result), encoding="utf-8")
    return {
        "json": {"file": json_path.name, "sha256": _sha256(json_path)},
        "junit": {"file": junit_path.name, "sha256": _sha256(junit_path)},
        "markdown": {"file": markdown_path.name, "sha256": _sha256(markdown_path)},
    }


def _write_json(output_dir: Path, name: str, value: dict) -> dict:
    path = output_dir / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"file": path.name, "sha256": _sha256(path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    source_root = args.source_root.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir == source_root or source_root in output_dir.parents:
        raise SystemExit("output-dir must be outside the candidate source tree")
    lock = validate_measurement_lock(json.loads(args.lock.read_text(encoding="utf-8")))
    prereg = json.loads(args.preregistration.read_text(encoding="utf-8"))
    prospective = prospective_git_receipt(args.lock, args.preregistration)
    validate_registration_repository(lock, prereg, prospective)
    environment = measurement_environment()
    if lock.get("environment") != environment:
        raise SystemExit("measurement runtime does not match the frozen environment")
    head = _git(source_root, "rev-parse", "HEAD")
    dirty = bool(_git(source_root, "status", "--porcelain"))
    if (
        head != lock.get("candidate_git_head")
        or dirty
    ):
        raise SystemExit("candidate source binding mismatch or dirty worktree")
    if _sha256(args.preregistration) != lock.get("preregistration_sha256"):
        raise SystemExit("preregistration hash does not match the measurement lock")
    if prereg.get("registered_at") >= _now():
        raise SystemExit("preregistration is not earlier than measurement")

    deepeval_spec = (
        source_root
        / "docs"
        / "receipts"
        / "lakatotree-trajectory-qualification"
        / "qualification-spec-v2.json"
    )
    fixture_dir = args.fixture_dir.resolve() if args.fixture_dir is not None else None
    benchmark_provenance = tier0_provenance(fixture_dir=fixture_dir)
    benchmark_hashes = benchmark_provenance["files"]
    preflight_bindings = {
        "benchmark_definition_sha256": benchmark_provenance["benchmark_definition_sha256"],
        "code_manifest_sha256": benchmark_provenance["code_manifest_sha256"],
        "manifest_sha256": benchmark_hashes["manifest"],
        "gate_spec_sha256": benchmark_hashes["trajectory_gate"],
        "events_sha256": benchmark_hashes["trajectory_events"],
        "runner_sha256": benchmark_hashes["runner"],
        "deepeval_spec_sha256": _sha256(deepeval_spec),
    }
    preflight_mismatches = {
        key: {"locked": lock.get(key), "observed": value}
        for key, value in preflight_bindings.items()
        if lock.get(key) != value
    }
    if preflight_mismatches:
        raise SystemExit(f"preflight binding mismatch: {preflight_mismatches}")

    output_dir.mkdir(parents=True, exist_ok=False)
    positive = run_tier0_benchmark(
        fixture_dir=fixture_dir,
        seed=lock["seed"],
        repetitions=lock["repetitions"],
    )
    validate_tier0_result(positive, fixture_dir=fixture_dir)
    if (
        positive["tier"] != lock.get("tier")
        or positive["seed"] != lock.get("seed")
        or positive["repetitions"] != lock.get("repetitions")
    ):
        raise SystemExit("run parameters do not match the measurement lock")
    positive_at = _now()
    positive_files = _write_result(output_dir, "tier0-positive", positive)

    negative = run_tier0_benchmark(
        fixture_dir=fixture_dir,
        seed=lock["seed"],
        repetitions=lock["repetitions"],
        fault_injection="disable-confirm-rounds",
    )
    validate_tier0_result(negative, fixture_dir=fixture_dir)
    negative_at = _now()
    negative_files = _write_result(output_dir, "tier0-negative", negative)

    restored = run_tier0_benchmark(
        fixture_dir=fixture_dir,
        seed=lock["seed"],
        repetitions=lock["repetitions"],
    )
    validate_tier0_result(restored, fixture_dir=fixture_dir)
    restored_at = _now()
    restored_files = _write_result(output_dir, "tier0-restored", restored)

    if not positive["passed"] or negative["passed"] or not restored["passed"]:
        raise SystemExit("positive/negative/restored oracle sequence did not hold")
    if canonical_json(positive) != canonical_json(restored):
        raise SystemExit("restored positive is not byte-identical to the first positive")
    failed = [
        row["id"] for row in negative["scenarios"] if row["oracle_match_rate"] != 1.0
    ]
    if failed != ["late-offender-confirm"]:
        raise SystemExit(f"negative control was not localized: {failed!r}")

    provenance = positive["provenance"]
    required_bindings = {
        "benchmark_definition_sha256": provenance["benchmark_definition_sha256"],
        "code_manifest_sha256": provenance["code_manifest_sha256"],
        "manifest_sha256": provenance["files"]["manifest"],
        "gate_spec_sha256": provenance["files"]["trajectory_gate"],
        "events_sha256": provenance["files"]["trajectory_events"],
        "runner_sha256": provenance["files"]["runner"],
    }
    mismatches = {
        key: {"locked": lock.get(key), "observed": value}
        for key, value in required_bindings.items()
        if lock.get(key) != value
    }
    if mismatches:
        raise SystemExit(f"benchmark binding mismatch: {mismatches}")

    deepeval = measure_deepeval(source_root, deepeval_spec)
    deepeval_metrics = validate_deepeval_measurement(
        deepeval,
        expected_head=head,
        expected_spec_sha256=lock["deepeval_spec_sha256"],
        expected_version=lock["deepeval_version"],
    )
    deepeval_file = _write_json(output_dir, "deepeval-candidate.json", deepeval)
    deepeval_negative = expected_deepeval_mismatch(deepeval)
    mismatch_rejected = False
    try:
        validate_deepeval_measurement(
            deepeval_negative,
            expected_head=head,
            expected_spec_sha256=lock["deepeval_spec_sha256"],
            expected_version=lock["deepeval_version"],
        )
    except EvidenceIntegrityError:
        mismatch_rejected = True
    if not mismatch_rejected:
        raise SystemExit("injected DeepEval mismatch was not rejected")
    deepeval_negative_file = _write_json(
        output_dir,
        "deepeval-injected-mismatch.json",
        deepeval_negative,
    )

    sequence = {
        "schema": SCHEMA,
        "source": {"git_head": head, "dirty": False},
        "environment": environment,
        "measurement_lock_sha256": _sha256(args.lock),
        "preregistration_sha256": _sha256(args.preregistration),
        "benchmark_definition_sha256": provenance["benchmark_definition_sha256"],
        "prospective_registration": prospective,
        "measurements": [
            {"role": "positive", "measured_at": positive_at, "artifacts": positive_files},
            {"role": "negative", "measured_at": negative_at, "artifacts": negative_files},
            {"role": "restored", "measured_at": restored_at, "artifacts": restored_files},
        ],
        "deepeval": {
            "measured_at": deepeval["measured_at"],
            "candidate": deepeval_file,
            "injected_mismatch": deepeval_negative_file,
            "injected_mismatch_rejected": True,
            "computed_metrics": deepeval_metrics,
        },
    }
    sequence_path = output_dir / "measurement-sequence.json"
    sequence_path.write_text(
        json.dumps(sequence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(sequence_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
