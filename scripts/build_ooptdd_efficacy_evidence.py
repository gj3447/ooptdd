#!/usr/bin/env python3
"""Validate frozen efficacy measurements and build verdict-free LakatoTree evidence."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ooptdd.benchmark import (
    DEFAULT_FIXTURE_DIR,
    canonical_json,
    render_benchmark_junit,
    render_benchmark_markdown,
    validate_tier0_result,
)
from ooptdd.evidence_integrity import (
    EvidenceIntegrityError,
    measurement_environment,
    prospective_git_receipt,
    validate_chronology,
    validate_deepeval_measurement,
    validate_deepeval_mismatch,
    validate_measurement_lock,
    validate_registration_repository,
)

REPORT_SCHEMA = "ooptdd-efficacy-integrity-report/v1"
RECORD_SCHEMA = "lakato-evidence-record/v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_new(path: Path, payload: bytes) -> None:
    """Create a result exactly once; never replace a validated input or prior receipt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def _github_json(path: str) -> dict:
    """Read one fixed-origin GitHub API object, optionally using the ambient CI token."""
    url = f"https://api.github.com{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ooptdd-evidence-builder",
    }
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(  # noqa: S310 - origin is fixed above
            urllib.request.Request(url, headers=headers), timeout=30
        ) as response:
            value = json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise EvidenceIntegrityError(f"GitHub Actions live verification failed: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceIntegrityError("GitHub Actions API response must be an object")
    return value


def _live_ci_receipt(
    run_id: int,
    *,
    fetcher: Callable[[str], dict] = _github_json,
) -> dict:
    """Normalize the relevant run, job step, and artifact from GitHub's live API."""
    base = "/repos/gj3447/ooptdd/actions/runs"
    run = fetcher(f"{base}/{run_id}")
    jobs_response = fetcher(f"{base}/{run_id}/jobs?per_page=100")
    artifacts_response = fetcher(f"{base}/{run_id}/artifacts?per_page=100")
    jobs = jobs_response.get("jobs")
    artifacts = artifacts_response.get("artifacts")
    if not isinstance(jobs, list) or jobs_response.get("total_count") != len(jobs):
        raise EvidenceIntegrityError("GitHub jobs response is incomplete or malformed")
    if not isinstance(artifacts, list) or artifacts_response.get("total_count") != len(artifacts):
        raise EvidenceIntegrityError("GitHub artifacts response is incomplete or malformed")
    qualification = [job for job in jobs if isinstance(job, dict)
                     and job.get("name") == "lakatotree-qualification"]
    retained = {
        name: [item for item in artifacts if isinstance(item, dict) and item.get("name") == name]
        for name in ("tier0-arrival-benchmark", "deepeval-heldout-v2")
    }
    if len(qualification) != 1 or any(len(items) != 1 for items in retained.values()):
        raise EvidenceIntegrityError(
            "GitHub run lacks unique qualification job or retained evidence artifacts"
        )
    steps = qualification[0].get("steps")
    assertion = [
        step for step in (steps if isinstance(steps, list) else [])
        if isinstance(step, dict)
        and step.get("name") == "Recompute and assert the DeepEval artifact"
    ]
    if len(assertion) != 1:
        raise EvidenceIntegrityError("GitHub run lacks the unique DeepEval assertion step")
    repository = run.get("repository") or {}
    return {
        "schema": "ooptdd-actions-receipt/v1",
        "repository": repository.get("full_name"),
        "workflow_path": run.get("path"),
        "run_id": run.get("id"),
        "head_sha": run.get("head_sha"),
        "conclusion": run.get("conclusion"),
        "html_url": run.get("html_url"),
        "jobs": [{
            "name": qualification[0].get("name"),
            "conclusion": qualification[0].get("conclusion"),
            "steps": [{
                "name": assertion[0].get("name"),
                "conclusion": assertion[0].get("conclusion"),
            }],
        }],
        "artifacts": [
            {
                "name": item[0].get("name"),
                "digest": item[0].get("digest"),
                "expired": item[0].get("expired"),
            }
            for name, item in sorted(retained.items())
        ],
    }


def _validate_ci_receipt(
    receipt: dict,
    *,
    expected_head: str,
    fetcher: Callable[[str], dict] = _github_json,
) -> dict:
    """Validate the normalized receipt captured from GitHub's Actions API.

    This is a durable binding to a completed run, not a claim that local workflow text is
    wired. The operator still captures the receipt from GitHub; its URL/run id/digest are
    retained so the independent consumer can audit the external source.
    """
    if not isinstance(receipt, dict) or receipt.get("schema") != "ooptdd-actions-receipt/v1":
        raise EvidenceIntegrityError("CI receipt schema mismatch")
    run_id = receipt.get("run_id")
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise EvidenceIntegrityError("CI receipt run_id must be a positive integer")
    live = _live_ci_receipt(run_id, fetcher=fetcher)
    if receipt != live:
        raise EvidenceIntegrityError("CI receipt does not exactly match live GitHub Actions state")
    if (
        receipt.get("repository") != "gj3447/ooptdd"
        or receipt.get("workflow_path") != ".github/workflows/ci.yml"
        or receipt.get("head_sha") != expected_head
        or receipt.get("conclusion") != "success"
        or not isinstance(receipt.get("html_url"), str)
        or f"/actions/runs/{receipt['run_id']}" not in receipt["html_url"]
    ):
        raise EvidenceIntegrityError("CI receipt does not bind a successful candidate run")
    jobs = receipt.get("jobs")
    if not isinstance(jobs, list):
        raise EvidenceIntegrityError("CI receipt jobs must be a list")
    qualification = [job for job in jobs if isinstance(job, dict)
                     and job.get("name") == "lakatotree-qualification"]
    if len(qualification) != 1 or qualification[0].get("conclusion") != "success":
        raise EvidenceIntegrityError("DeepEval qualification job did not succeed in CI")
    steps = qualification[0].get("steps")
    asserted = any(
        isinstance(step, dict)
        and step.get("name") == "Recompute and assert the DeepEval artifact"
        and step.get("conclusion") == "success"
        for step in (steps if isinstance(steps, list) else [])
    )
    if not asserted:
        raise EvidenceIntegrityError("DeepEval assertion step did not succeed in CI")
    artifacts = receipt.get("artifacts")
    artifacts = artifacts if isinstance(artifacts, list) else []
    digests = {}
    for name in ("tier0-arrival-benchmark", "deepeval-heldout-v2"):
        matching = [item for item in artifacts if isinstance(item, dict)
                    and item.get("name") == name]
        if len(matching) != 1:
            raise EvidenceIntegrityError(f"Actions artifact is absent or ambiguous: {name}")
        digest = matching[0].get("digest")
        if (
            matching[0].get("expired") is not False
            or not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest.removeprefix("sha256:")) != 64
            or any(char not in "0123456789abcdef" for char in digest.removeprefix("sha256:"))
        ):
            raise EvidenceIntegrityError(f"Actions artifact digest is not a live SHA-256: {name}")
        digests[name] = digest
    return {
        "run_id": receipt["run_id"],
        "html_url": receipt["html_url"],
        "artifact_digests": digests,
        "deepeval_assertion_step": "success",
    }


def _git(source_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source_root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _artifact(
    output_dir: Path,
    measurement: dict,
    kind: str = "json",
    *,
    expected_name: str | None = None,
) -> Path:
    meta = measurement["artifacts"][kind]
    path = (output_dir / meta["file"]).resolve()
    if path.parent != output_dir.resolve():
        raise EvidenceIntegrityError("artifact path escapes the measurement directory")
    if expected_name is not None and path.name != expected_name:
        raise EvidenceIntegrityError(
            f"artifact name drift: {path.name!r} != {expected_name!r}"
        )
    if _sha256(path) != meta["sha256"]:
        raise EvidenceIntegrityError(f"artifact hash mismatch: {path.name}")
    return path


def _rejected(callable_) -> bool:
    try:
        callable_()
    except EvidenceIntegrityError:
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--measurement-dir", type=Path, required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--integrity-output", type=Path)
    parser.add_argument("--evidence-output", type=Path)
    parser.add_argument("--ci-receipt", type=Path)
    args = parser.parse_args(argv)

    source_root = args.source_root.resolve()
    measurement_dir = args.measurement_dir.resolve()
    bundle_dir = args.bundle_dir.resolve()
    if (
        bundle_dir.exists()
        or bundle_dir == source_root
        or source_root in bundle_dir.parents
        or bundle_dir == measurement_dir
        or measurement_dir in bundle_dir.parents
    ):
        raise SystemExit("bundle-dir must be new and outside candidate and measurement trees")
    integrity_output = bundle_dir / "integrity-report.json"
    evidence_output = bundle_dir / "evidence-record.json"
    if args.integrity_output is not None and args.integrity_output.resolve() != integrity_output:
        raise SystemExit("integrity-output must be bundle-dir/integrity-report.json")
    if args.evidence_output is not None and args.evidence_output.resolve() != evidence_output:
        raise SystemExit("evidence-output must be bundle-dir/evidence-record.json")
    args.bundle_dir = bundle_dir
    args.measurement_dir = measurement_dir
    args.integrity_output = integrity_output
    args.evidence_output = evidence_output
    lock = validate_measurement_lock(_read(args.lock))
    prereg = _read(args.preregistration)
    sequence = _read(measurement_dir / "measurement-sequence.json")
    head = _git(source_root, "rev-parse", "HEAD")
    dirty = bool(_git(source_root, "status", "--porcelain"))
    if (
        head != lock.get("candidate_git_head")
        or dirty
    ):
        raise SystemExit("candidate source binding mismatch or dirty worktree")
    if sequence.get("schema") != "ooptdd-efficacy-measurement-sequence/v1":
        raise SystemExit("measurement sequence schema mismatch")
    if sequence.get("source") != {"git_head": head, "dirty": False}:
        raise SystemExit("measurement sequence source binding mismatch")
    if (
        sequence.get("environment") != lock.get("environment")
        or measurement_environment() != lock.get("environment")
    ):
        raise SystemExit("measurement environment binding mismatch")
    if _sha256(args.lock) != sequence.get("measurement_lock_sha256"):
        raise SystemExit("measurement lock hash mismatch")
    if _sha256(args.preregistration) != sequence.get("preregistration_sha256"):
        raise SystemExit("preregistration hash mismatch")
    if _sha256(args.preregistration) != lock.get("preregistration_sha256"):
        raise SystemExit("measurement lock does not bind the supplied preregistration")
    prospective = prospective_git_receipt(args.lock, args.preregistration)
    validate_registration_repository(lock, prereg, prospective)
    recorded_prospective = sequence.get("prospective_registration")
    if not isinstance(recorded_prospective, dict):
        raise SystemExit("measurement sequence lacks prospective git receipt")
    for key in ("schema", "repository", "preregistration_is_ancestor"):
        if recorded_prospective.get(key) != prospective.get(key):
            raise SystemExit("prospective git receipt drifted after measurement")
    for role in ("preregistration", "measurement_lock"):
        recorded = recorded_prospective.get(role) or {}
        current = prospective.get(role) or {}
        for key in ("path", "commit", "sha256"):
            if recorded.get(key) != current.get(key):
                raise SystemExit("prospective git object binding drifted after measurement")
        recorded_refs = set(recorded.get("published_refs") or [])
        current_refs = set(current.get("published_refs") or [])
        if not recorded_refs <= current_refs:
            raise SystemExit("prospective git publication proof disappeared")

    measurements = sequence["measurements"]
    if [item.get("role") for item in measurements] != ["positive", "negative", "restored"]:
        raise SystemExit("measurement roles/order must be positive, negative, restored")
    extensions = {"json": "json", "junit": "xml", "markdown": "md"}
    for measurement in measurements:
        stem = f"tier0-{measurement['role']}"
        for kind, extension in extensions.items():
            _artifact(
                args.measurement_dir,
                measurement,
                kind,
                expected_name=f"{stem}.{extension}",
            )
    positive_path = _artifact(args.measurement_dir, measurements[0])
    negative_path = _artifact(args.measurement_dir, measurements[1])
    restored_path = _artifact(args.measurement_dir, measurements[2])
    positive, negative, restored = map(_read, (positive_path, negative_path, restored_path))
    validate_tier0_result(positive, fixture_dir=args.fixture_dir)
    validate_tier0_result(negative, fixture_dir=args.fixture_dir)
    validate_tier0_result(restored, fixture_dir=args.fixture_dir)
    for measurement, result in zip(measurements, (positive, negative, restored), strict=True):
        json_path = _artifact(args.measurement_dir, measurement, expected_name=(
            f"tier0-{measurement['role']}.json"
        ))
        junit_path = _artifact(
            args.measurement_dir,
            measurement,
            "junit",
            expected_name=f"tier0-{measurement['role']}.xml",
        )
        markdown_path = _artifact(
            args.measurement_dir,
            measurement,
            "markdown",
            expected_name=f"tier0-{measurement['role']}.md",
        )
        if json_path.read_text(encoding="utf-8") != canonical_json(result):
            raise SystemExit(f"non-canonical benchmark JSON: {json_path.name}")
        if junit_path.read_text(encoding="utf-8") != render_benchmark_junit(result):
            raise SystemExit(f"JUnit is not a pure canonical projection: {junit_path.name}")
        if markdown_path.read_text(encoding="utf-8") != render_benchmark_markdown(result):
            raise SystemExit(f"Markdown is not a pure canonical projection: {markdown_path.name}")
    for result in (positive, negative, restored):
        if (
            result["tier"] != lock.get("tier")
            or result["seed"] != lock.get("seed")
            or result["repetitions"] != lock.get("repetitions")
            or result["provenance"]["benchmark_definition_sha256"]
            != lock.get("benchmark_definition_sha256")
        ):
            raise SystemExit("measurement result does not match the frozen lock")
    if not positive["passed"] or negative["passed"] or not restored["passed"]:
        raise SystemExit("measurement polarity mismatch")
    if positive_path.read_bytes() != restored_path.read_bytes():
        raise SystemExit("restored artifact is not byte-identical to positive")
    negative_failures = [
        row["id"] for row in negative["scenarios"] if row["oracle_match_rate"] != 1.0
    ]
    if negative_failures != ["late-offender-confirm"]:
        raise SystemExit(f"negative control is not localized: {negative_failures!r}")

    forged_metric = copy.deepcopy(positive)
    forged_metric["metrics"]["required_oracle_match_rate"]["value"] = 99.0
    forged_binding = copy.deepcopy(positive)
    forged_binding["provenance"]["files"]["manifest"] = "0" * 64
    workflow = source_root / ".github" / "workflows" / "ci.yml"
    validator = source_root / "scripts" / "validate_trajectory_evidence.py"
    workflow_text = workflow.read_text(encoding="utf-8")
    deepeval_meta = sequence.get("deepeval") or {}
    deepeval_path = (
        args.measurement_dir / (deepeval_meta.get("candidate") or {}).get("file", "")
    ).resolve()
    deepeval_negative_path = (
        args.measurement_dir / (deepeval_meta.get("injected_mismatch") or {}).get("file", "")
    ).resolve()
    if (
        deepeval_path.parent != args.measurement_dir.resolve()
        or deepeval_path.name != "deepeval-candidate.json"
        or deepeval_negative_path.parent != args.measurement_dir.resolve()
        or deepeval_negative_path.name != "deepeval-injected-mismatch.json"
        or not deepeval_path.is_file()
        or _sha256(deepeval_path) != deepeval_meta["candidate"]["sha256"]
        or not deepeval_negative_path.is_file()
        or _sha256(deepeval_negative_path)
        != deepeval_meta["injected_mismatch"]["sha256"]
    ):
        raise SystemExit("DeepEval measurement artifact hash mismatch")
    deepeval_probe = validate_deepeval_measurement(
        _read(deepeval_path),
        expected_head=head,
        expected_spec_sha256=lock["deepeval_spec_sha256"],
        expected_version=lock["deepeval_version"],
    )
    deepeval_negative = _read(deepeval_negative_path)
    validate_deepeval_mismatch(_read(deepeval_path), deepeval_negative)
    deep_mismatch_rejected = _rejected(
        lambda: validate_deepeval_measurement(
            deepeval_negative,
            expected_head=head,
            expected_spec_sha256=lock["deepeval_spec_sha256"],
            expected_version=lock["deepeval_version"],
        )
    )
    if not deep_mismatch_rejected:
        raise SystemExit("exact DeepEval negative control was not rejected")
    if deepeval_meta.get("computed_metrics") != deepeval_probe:
        raise SystemExit("stored DeepEval computed metrics do not match raw observations")
    deepeval_record = _read(deepeval_path)
    if deepeval_meta.get("measured_at") != deepeval_record.get("measured_at"):
        raise SystemExit("DeepEval sequence timestamp does not match the raw artifact")
    validate_chronology(
        prereg["registered_at"],
        *measurements,
        deepeval_record,
    )
    ci_evidence = None
    if args.ci_receipt is not None:
        ci_evidence = _validate_ci_receipt(_read(args.ci_receipt), expected_head=head)
    observations = [
        {
            "gap_id": "nonvacuous_trajectory_mutation",
            "resolved": positive["metrics"]["M4_trajectory_mutation"]["eligible"] >= 1
            and positive["metrics"]["M4_trajectory_mutation"]["score_status"] == "measured"
            and not positive["metrics"]["M4_trajectory_mutation"]["canary_survived"],
            "evidence": positive["metrics"]["M4_trajectory_mutation"],
        },
        {
            "gap_id": "observation_first_aggregate_recomputation",
            "resolved": _rejected(
                lambda: validate_tier0_result(forged_metric, fixture_dir=args.fixture_dir)
            ),
            "evidence": {"forged_required_oracle_match_rate": 99.0, "rejected": True},
        },
        {
            "gap_id": "source_spec_and_file_binding",
            "resolved": _rejected(
                lambda: validate_tier0_result(forged_binding, fixture_dir=args.fixture_dir)
            )
            and sequence["benchmark_definition_sha256"]
            == lock["benchmark_definition_sha256"],
            "evidence": {
                "candidate_git_head": head,
                "benchmark_definition_sha256": sequence["benchmark_definition_sha256"],
                "forged_manifest_hash_rejected": True,
            },
        },
        {
            "gap_id": "deepeval_artifact_asserted_in_ci",
            "resolved": validator.is_file()
            and "validate_trajectory_evidence.py deepeval" in workflow_text
            and "Recompute and assert the DeepEval artifact" in workflow_text
            and deepeval_probe["deepeval_oracle_agreement_rate"] == 1.0
            and deep_mismatch_rejected
            and deepeval_meta.get("injected_mismatch_rejected") is True
            and ci_evidence is not None,
            "evidence": {
                "candidate_artifact_sha256": _sha256(deepeval_path),
                "injected_mismatch_rejected": deep_mismatch_rejected,
                "validator_sha256": _sha256(validator),
                "workflow_sha256": _sha256(workflow),
                "actions": ci_evidence or {"status": "wired_pending_completed_run"},
            },
        },
    ]
    unresolved = sum(not item["resolved"] for item in observations)
    integrity = {
        "schema": REPORT_SCHEMA,
        "candidate_git_head": head,
        "observations": observations,
        "unresolved_evidence_integrity_gaps": unresolved,
        "tier0_required_oracle_match_rate": positive["metrics"][
            "required_oracle_match_rate"
        ]["value"],
        "negative_control_failures": negative_failures,
        "restored_byte_identical": True,
    }
    integrity_bytes = _json_bytes(integrity)
    integrity_sha256 = hashlib.sha256(integrity_bytes).hexdigest()

    evidence = {
        "schema": RECORD_SCHEMA,
        "programme": prereg["programme"],
        "conjecture": prereg["branch"],
        "preregistration": {
            "registered_before_measurement": True,
            "registered_at": prereg["registered_at"],
            "direction": prereg["prediction"]["direction"],
            "noise_band": prereg["prediction"]["noise_band"],
            "predicted": {
                "metric": prereg["prediction"]["metric"],
                "value": prereg["prediction"]["baseline"],
                "unit": "count",
            },
            "kill_condition": prereg["kill_condition"],
            "target": prereg["prediction"]["target"],
            "max_acceptable": prereg["prediction"]["target"],
        },
        "measurement": {
            "metric": "unresolved_evidence_integrity_gaps",
            "value": unresolved,
            "unit": "count",
            "derived": {
                "tier0_required_oracle_match_rate": integrity[
                    "tier0_required_oracle_match_rate"
                ]
            },
            "primary_source_sha256": integrity_sha256,
            "novel_measurement": {
                "metric": "tier0_required_oracle_match_rate",
                "value": integrity["tier0_required_oracle_match_rate"],
                "direction": "higher",
                "threshold": prereg["novel_target"]["threshold"],
                "source_sha256": _sha256(positive_path),
                "repetitions": positive["repetitions"],
            },
        },
        "provenance": {
            "grounded": True,
            "inputs": [
                {"name": "integrity-report", "source": args.integrity_output.name,
                 "sha256": integrity_sha256},
                {"name": "tier0-positive", "source": positive_path.name,
                 "sha256": _sha256(positive_path)},
                {"name": "tier0-positive-junit", "source": "tier0-positive.xml",
                 "sha256": _sha256(args.measurement_dir / "tier0-positive.xml")},
                {"name": "tier0-positive-markdown", "source": "tier0-positive.md",
                 "sha256": _sha256(args.measurement_dir / "tier0-positive.md")},
                {"name": "tier0-negative", "source": negative_path.name,
                 "sha256": _sha256(negative_path)},
                {"name": "tier0-negative-junit", "source": "tier0-negative.xml",
                 "sha256": _sha256(args.measurement_dir / "tier0-negative.xml")},
                {"name": "tier0-negative-markdown", "source": "tier0-negative.md",
                 "sha256": _sha256(args.measurement_dir / "tier0-negative.md")},
                {"name": "tier0-restored", "source": restored_path.name,
                 "sha256": _sha256(restored_path)},
                {"name": "tier0-restored-junit", "source": "tier0-restored.xml",
                 "sha256": _sha256(args.measurement_dir / "tier0-restored.xml")},
                {"name": "tier0-restored-markdown", "source": "tier0-restored.md",
                 "sha256": _sha256(args.measurement_dir / "tier0-restored.md")},
                {"name": "measurement-sequence", "source": "measurement-sequence.json",
                 "sha256": _sha256(args.measurement_dir / "measurement-sequence.json")},
                {"name": "deepeval-candidate", "source": deepeval_path.name,
                 "sha256": _sha256(deepeval_path)},
                {"name": "deepeval-injected-mismatch", "source": deepeval_negative_path.name,
                 "sha256": _sha256(deepeval_negative_path)},
                {"name": "measurement-lock", "source": args.lock.name,
                 "sha256": _sha256(args.lock)},
                {"name": "preregistration", "source": args.preregistration.name,
                 "sha256": _sha256(args.preregistration)},
                *([{"name": "github-actions-receipt", "source": args.ci_receipt.name,
                    "sha256": _sha256(args.ci_receipt)}]
                  if args.ci_receipt is not None else []),
            ],
        },
        "harness": {
            "script": "scripts/build_ooptdd_efficacy_evidence.py",
            "git_commit": head,
            "benchmark_definition_sha256": sequence["benchmark_definition_sha256"],
            "environment": "python>=3.10; deterministic Tier-0; OMD excluded",
        },
    }
    bundle_inputs = [
        args.measurement_dir / name
        for name in (
            "tier0-positive.json", "tier0-positive.xml", "tier0-positive.md",
            "tier0-negative.json", "tier0-negative.xml", "tier0-negative.md",
            "tier0-restored.json", "tier0-restored.xml", "tier0-restored.md",
            "measurement-sequence.json", "deepeval-candidate.json",
            "deepeval-injected-mismatch.json",
        )
    ] + [args.lock.resolve(), args.preregistration.resolve()]
    if args.ci_receipt is not None:
        bundle_inputs.append(args.ci_receipt.resolve())
    names = [path.name for path in bundle_inputs]
    if len(names) != len(set(names)):
        raise SystemExit("bundle input basenames collide")
    args.bundle_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(
        prefix=f".{args.bundle_dir.name}.tmp-",
        dir=args.bundle_dir.parent,
    ))
    try:
        for source in bundle_inputs:
            _write_new(temporary / source.name, source.read_bytes())
        _write_new(temporary / args.integrity_output.name, integrity_bytes)
        _write_new(temporary / args.evidence_output.name, _json_bytes(evidence))
        temporary.rename(args.bundle_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(json.dumps({"unresolved": unresolved, "evidence": str(args.evidence_output)}))
    return 0 if unresolved == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
