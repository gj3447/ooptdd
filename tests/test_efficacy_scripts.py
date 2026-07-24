"""End-to-end contracts for the frozen efficacy measurement scripts."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from unittest import mock

import pytest

import ooptdd.benchmark as benchmark_module
from ooptdd.benchmark import (
    canonical_json,
    render_benchmark_junit,
    render_benchmark_markdown,
)
from ooptdd.evidence_integrity import (
    EvidenceIntegrityError,
    measurement_environment,
    prospective_git_receipt,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURE_DIR = REPO_ROOT / "benchmarks" / "arrival" / "v0"


def _load_script(stem: str) -> ModuleType:
    module_name = f"_ooptdd_test_{stem}"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / f"{stem}.py")
    assert spec is not None and spec.loader is not None
    scripts_path = str(SCRIPTS_DIR)
    sys.path.insert(0, scripts_path)
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts_path)
    return module


MEASUREMENT_SCRIPT = _load_script("run_efficacy_measurement")
BUILDER_SCRIPT = _load_script("build_ooptdd_efficacy_evidence")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


@dataclass(frozen=True)
class EfficacyBundle:
    root: Path
    source_root: Path
    governance_root: Path
    governance_origin: Path
    preregistration: Path
    lock: Path
    measurement_dir: Path
    head: str


def _deepeval_record(source_root: Path, spec_path: Path, head: str) -> dict:
    observations = [
        {
            "name": "safe",
            "expected_score": 1.0,
            "observed_score": 1.0,
            "expected_success": True,
            "observed_success": True,
            "matched": True,
            "reason": "safe trajectory accepted",
            "error": None,
        },
        {
            "name": "destructive",
            "expected_score": 0.0,
            "observed_score": 0.0,
            "expected_success": False,
            "observed_success": False,
            "matched": True,
            "reason": "destructive trajectory rejected",
            "error": None,
        },
        {
            "name": "corrupt",
            "expected_score": 0.0,
            "observed_score": 0.0,
            "expected_success": False,
            "observed_success": False,
            "matched": True,
            "reason": "corrupt trajectory rejected",
            "error": None,
        },
    ]
    return {
        "schema_version": "ooptdd-deepeval-heldout/v1",
        "measured_at": "2026-07-23T00:05:00Z",
        "source": {"root": str(source_root), "git_head": head, "dirty": False},
        "spec": {"path": str(spec_path), "sha256": _sha256(spec_path)},
        "environment": {
            "python": sys.version.split()[0],
            "platform": "test-platform",
            "deepeval": "4.0.7",
        },
        "metrics": {
            "actual_deepeval_trajectory_pass_rate": 1.0,
            "cases_total": 3,
            "cases_matched": 3,
        },
        "observations": observations,
    }


@pytest.fixture(scope="module")
def efficacy_bundle(tmp_path_factory: pytest.TempPathFactory) -> EfficacyBundle:
    root = tmp_path_factory.mktemp("efficacy-scripts")
    source_root = root / "candidate"
    deepeval_spec = (
        source_root
        / "docs"
        / "receipts"
        / "lakatotree-trajectory-qualification"
        / "qualification-spec-v2.json"
    )
    deepeval_spec.parent.mkdir(parents=True)
    deepeval_spec.write_text('{"fixture": "deepeval"}\n', encoding="utf-8")
    workflow = source_root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: CI\n"
        "# Recompute and assert the DeepEval artifact\n"
        "- run: python scripts/validate_trajectory_evidence.py deepeval\n",
        encoding="utf-8",
    )
    validator = source_root / "scripts" / "validate_trajectory_evidence.py"
    validator.parent.mkdir(parents=True)
    validator.write_text("# frozen test validator\n", encoding="utf-8")
    _git(source_root, "init", "-q")
    _git(source_root, "config", "user.name", "ooptdd-test")
    _git(source_root, "config", "user.email", "ooptdd-test@example.invalid")
    head = _commit(source_root, "candidate fixture")

    governance_origin = root / "governance-origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", str(governance_origin)],
        check=True,
        capture_output=True,
    )
    governance_root = root / "governance"
    governance_root.mkdir()
    _git(governance_root, "init", "-q", "-b", "main")
    _git(governance_root, "config", "user.name", "ooptdd-test")
    _git(governance_root, "config", "user.email", "ooptdd-test@example.invalid")
    _git(governance_root, "remote", "add", "origin", str(governance_origin))
    preregistration = governance_root / "efficacy" / "preregistration.json"
    _write(
        preregistration,
        {
            "schema": "ooptdd-efficacy-preregistration/v1",
            "programme": "ooptdd-efficacy-test",
            "branch": "test-candidate",
            "registered_at": "2026-07-23T00:00:00Z",
            "sources": {"lakatotree_repository": str(governance_origin)},
            "prediction": {
                "metric": "unresolved_evidence_integrity_gaps",
                "direction": "lower",
                "baseline": 4,
                "noise_band": 0,
                "target": 0,
            },
            "novel_target": {
                "metric": "tier0_required_oracle_match_rate",
                "threshold": 1.0,
            },
            "kill_condition": "kill on any unresolved integrity gap",
        },
    )
    _commit(governance_root, "prospective preregistration")
    _git(governance_root, "push", "-qu", "origin", "main")

    benchmark_provenance = benchmark_module.tier0_provenance(fixture_dir=FIXTURE_DIR)
    benchmark_hashes = benchmark_provenance["files"]
    lock = governance_root / "efficacy" / "measurement-lock.json"
    _write(
        lock,
        {
            "schema": "ooptdd-efficacy-measurement-lock/v1",
            "candidate_git_head": head,
            "candidate_dirty": False,
            "registration_repository": str(governance_origin),
            "environment": measurement_environment(),
            "preregistration_sha256": _sha256(preregistration),
            "benchmark_definition_sha256": benchmark_provenance[
                "benchmark_definition_sha256"
            ],
            "code_manifest_sha256": benchmark_provenance["code_manifest_sha256"],
            "manifest_sha256": benchmark_hashes["manifest"],
            "gate_spec_sha256": benchmark_hashes["trajectory_gate"],
            "events_sha256": benchmark_hashes["trajectory_events"],
            "runner_sha256": benchmark_hashes["runner"],
            "deepeval_spec_sha256": _sha256(deepeval_spec),
            "deepeval_version": "4.0.7",
            "tier": "tier0-mechanics",
            "seed": 20260723,
            "repetitions": 1,
        },
    )
    _commit(governance_root, "prospective measurement lock")
    _git(governance_root, "push", "-q", "origin", "main")

    times = iter(
        [
            "2026-07-23T00:01:00Z",
            "2026-07-23T00:02:00Z",
            "2026-07-23T00:03:00Z",
            "2026-07-23T00:04:00Z",
        ]
    )
    measurement_dir = root / "measurement"
    with (
        mock.patch.object(MEASUREMENT_SCRIPT, "_now", side_effect=lambda: next(times)),
        mock.patch.object(
            MEASUREMENT_SCRIPT,
            "measure_deepeval",
            side_effect=lambda candidate, spec: _deepeval_record(candidate, spec, head),
        ),
    ):
        exit_code = MEASUREMENT_SCRIPT.main(
            [
                "--source-root",
                str(source_root),
                "--fixture-dir",
                str(FIXTURE_DIR),
                "--lock",
                str(lock),
                "--preregistration",
                str(preregistration),
                "--output-dir",
                str(measurement_dir),
            ]
        )
    assert exit_code == 0
    assert _git(source_root, "status", "--porcelain") == ""
    return EfficacyBundle(
        root=root,
        source_root=source_root,
        governance_root=governance_root,
        governance_origin=governance_origin,
        preregistration=preregistration,
        lock=lock,
        measurement_dir=measurement_dir,
        head=head,
    )


def _copy_candidate(bundle: EfficacyBundle, tmp_path: Path) -> Path:
    target = tmp_path / "candidate"
    shutil.copytree(bundle.source_root, target)
    return target


def _copy_measurement(bundle: EfficacyBundle, tmp_path: Path) -> Path:
    target = tmp_path / "measurement"
    shutil.copytree(bundle.measurement_dir, target)
    return target


def _published_governance_branch(
    bundle: EfficacyBundle,
    tmp_path: Path,
    *,
    lock_updates: dict | None = None,
) -> tuple[Path, Path]:
    governance = tmp_path / "governance"
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--branch",
            "main",
            str(bundle.governance_origin),
            str(governance),
        ],
        check=True,
        capture_output=True,
    )
    _git(governance, "config", "user.name", "ooptdd-test")
    _git(governance, "config", "user.email", "ooptdd-test@example.invalid")
    branch = f"case-{hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]}"
    _git(governance, "checkout", "-qb", branch)
    lock = governance / bundle.lock.relative_to(bundle.governance_root)
    preregistration = governance / bundle.preregistration.relative_to(
        bundle.governance_root
    )
    if lock_updates:
        lock_data = _read(lock)
        lock_data.update(lock_updates)
        _write(lock, lock_data)
        _commit(governance, "test measurement lock variant")
        _git(governance, "push", "-qu", "origin", branch)
    return lock, preregistration


def _builder_args(
    bundle: EfficacyBundle,
    *,
    source_root: Path | None = None,
    measurement_dir: Path | None = None,
    lock: Path | None = None,
    preregistration: Path | None = None,
    ci_receipt: Path | None = None,
    bundle_dir: Path | None = None,
    integrity_output: Path | None = None,
    evidence_output: Path | None = None,
    output_aliases: bool = False,
) -> list[str]:
    if bundle_dir is None:
        anchor = integrity_output or evidence_output
        assert anchor is not None
        bundle_dir = anchor.parent / "bundle"
    args = [
        "--source-root",
        str(source_root or bundle.source_root),
        "--fixture-dir",
        str(FIXTURE_DIR),
        "--lock",
        str(lock or bundle.lock),
        "--preregistration",
        str(preregistration or bundle.preregistration),
        "--measurement-dir",
        str(measurement_dir or bundle.measurement_dir),
        "--bundle-dir",
        str(bundle_dir),
    ]
    if output_aliases and integrity_output is not None:
        args.extend(["--integrity-output", str(integrity_output)])
    if output_aliases and evidence_output is not None:
        args.extend(["--evidence-output", str(evidence_output)])
    if ci_receipt is not None:
        args.extend(["--ci-receipt", str(ci_receipt)])
    return args


def _ci_receipt(head: str) -> dict:
    return {
        "schema": "ooptdd-actions-receipt/v1",
        "repository": "gj3447/ooptdd",
        "workflow_path": ".github/workflows/ci.yml",
        "head_sha": head,
        "conclusion": "success",
        "run_id": 123456789,
        "html_url": "https://github.com/gj3447/ooptdd/actions/runs/123456789",
        "jobs": [
            {
                "name": "lakatotree-qualification",
                "conclusion": "success",
                "steps": [
                    {
                        "name": "Recompute and assert the DeepEval artifact",
                        "conclusion": "success",
                    }
                ],
            }
        ],
        "artifacts": [
            {
                "name": "deepeval-heldout-v2",
                "expired": False,
                "digest": f"sha256:{'b' * 64}",
            },
            {
                "name": "tier0-arrival-benchmark",
                "expired": False,
                "digest": f"sha256:{'a' * 64}",
            }
        ],
    }


def _github_fetcher(receipt: dict):
    run_id = receipt["run_id"]
    base = f"/repos/gj3447/ooptdd/actions/runs/{run_id}"
    responses = {
        base: {
            "repository": {"full_name": receipt["repository"]},
            "path": receipt["workflow_path"],
            "id": run_id,
            "head_sha": receipt["head_sha"],
            "conclusion": receipt["conclusion"],
            "html_url": receipt["html_url"],
        },
        f"{base}/jobs?per_page=100": {
            "total_count": len(receipt["jobs"]),
            "jobs": copy.deepcopy(receipt["jobs"]),
        },
        f"{base}/artifacts?per_page=100": {
            "total_count": len(receipt["artifacts"]),
            "artifacts": copy.deepcopy(receipt["artifacts"]),
        },
    }

    def fetch(path: str) -> dict:
        return copy.deepcopy(responses[path])

    return fetch


def _expected_deepeval_negative(candidate: dict) -> dict:
    expected = copy.deepcopy(candidate)
    safe = next(row for row in expected["observations"] if row["name"] == "safe")
    safe["observed_success"] = False
    return expected


def test_measurement_clean_e2e_has_exact_negative_and_canonical_projections(
    efficacy_bundle: EfficacyBundle,
):
    sequence = _read(efficacy_bundle.measurement_dir / "measurement-sequence.json")
    assert sequence["source"] == {"git_head": efficacy_bundle.head, "dirty": False}
    prospective = sequence["prospective_registration"]
    assert prospective["repository"] == str(efficacy_bundle.governance_origin)
    assert prospective["preregistration_is_ancestor"] is True
    assert prospective["preregistration"]["sha256"] == _sha256(
        efficacy_bundle.preregistration
    )
    assert prospective["measurement_lock"]["sha256"] == _sha256(efficacy_bundle.lock)
    assert prospective["preregistration"]["commit"] != prospective["measurement_lock"][
        "commit"
    ]
    assert prospective["preregistration"]["published_refs"]
    assert prospective["measurement_lock"]["published_refs"]
    assert [row["role"] for row in sequence["measurements"]] == [
        "positive",
        "negative",
        "restored",
    ]

    results = []
    for measurement in sequence["measurements"]:
        artifacts = measurement["artifacts"]
        result = _read(efficacy_bundle.measurement_dir / artifacts["json"]["file"])
        results.append(result)
        assert (efficacy_bundle.measurement_dir / artifacts["json"]["file"]).read_text(
            encoding="utf-8"
        ) == canonical_json(result)
        assert (efficacy_bundle.measurement_dir / artifacts["junit"]["file"]).read_text(
            encoding="utf-8"
        ) == render_benchmark_junit(result)
        assert (efficacy_bundle.measurement_dir / artifacts["markdown"]["file"]).read_text(
            encoding="utf-8"
        ) == render_benchmark_markdown(result)
        for meta in artifacts.values():
            assert _sha256(efficacy_bundle.measurement_dir / meta["file"]) == meta["sha256"]

    positive, negative, restored = results
    assert positive == restored
    assert positive["passed"] is True
    assert negative["passed"] is False
    assert [
        row["id"] for row in negative["scenarios"] if row["oracle_match_rate"] != 1.0
    ] == ["late-offender-confirm"]

    deepeval = sequence["deepeval"]
    candidate = _read(efficacy_bundle.measurement_dir / deepeval["candidate"]["file"])
    injected = _read(
        efficacy_bundle.measurement_dir / deepeval["injected_mismatch"]["file"]
    )
    assert injected == _expected_deepeval_negative(candidate)
    assert deepeval["computed_metrics"]["deepeval_oracle_agreement_rate"] == 1.0


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("dirty", "candidate source binding mismatch or dirty worktree"),
        ("head", "candidate source binding mismatch or dirty worktree"),
        ("preregistration", "preregistration hash does not match"),
        ("runner", "preflight binding mismatch"),
    ],
)
def test_measurement_rejects_dirty_head_and_lock_hash_drift(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
    mode: str,
    message: str,
):
    source_root = _copy_candidate(efficacy_bundle, tmp_path)
    lock = efficacy_bundle.lock
    preregistration = efficacy_bundle.preregistration
    if mode == "dirty":
        (source_root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    elif mode == "head":
        (source_root / "new-head.txt").write_text("new head\n", encoding="utf-8")
        _commit(source_root, "advance candidate beyond lock")
    elif mode == "preregistration":
        lock, preregistration = _published_governance_branch(
            efficacy_bundle,
            tmp_path,
            lock_updates={"preregistration_sha256": "0" * 64},
        )
    elif mode == "runner":
        lock, preregistration = _published_governance_branch(
            efficacy_bundle,
            tmp_path,
            lock_updates={"runner_sha256": "0" * 64},
        )

    with pytest.raises(SystemExit, match=message):
        MEASUREMENT_SCRIPT.main(
            [
                "--source-root",
                str(source_root),
                "--fixture-dir",
                str(FIXTURE_DIR),
                "--lock",
                str(lock),
                "--preregistration",
                str(preregistration),
                "--output-dir",
                str(tmp_path / "output"),
            ]
        )
    assert not (tmp_path / "output").exists()


def test_measurement_rejects_output_inside_candidate_tree(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
):
    source_root = _copy_candidate(efficacy_bundle, tmp_path)
    with pytest.raises(SystemExit, match="outside the candidate source tree"):
        MEASUREMENT_SCRIPT.main(
            [
                "--source-root",
                str(source_root),
                "--fixture-dir",
                str(FIXTURE_DIR),
                "--lock",
                str(efficacy_bundle.lock),
                "--preregistration",
                str(efficacy_bundle.preregistration),
                "--output-dir",
                str(source_root / "evidence"),
            ]
        )


@pytest.mark.parametrize("state", ["uncommitted", "unpublished"])
def test_measurement_requires_published_prospective_lock(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
    state: str,
):
    source_root = _copy_candidate(efficacy_bundle, tmp_path)
    lock, preregistration = _published_governance_branch(efficacy_bundle, tmp_path)
    lock_data = _read(lock)
    lock_data["runner_sha256"] = "0" * 64
    _write(lock, lock_data)
    if state == "unpublished":
        _commit(lock.parents[1], "unpublished lock mutation")

    with pytest.raises(EvidenceIntegrityError, match="clean in git|published"):
        MEASUREMENT_SCRIPT.main(
            [
                "--source-root",
                str(source_root),
                "--fixture-dir",
                str(FIXTURE_DIR),
                "--lock",
                str(lock),
                "--preregistration",
                str(preregistration),
                "--output-dir",
                str(tmp_path / "output"),
            ]
        )


def test_measurement_rejects_registration_repository_drift(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
):
    source_root = _copy_candidate(efficacy_bundle, tmp_path)
    lock, preregistration = _published_governance_branch(
        efficacy_bundle,
        tmp_path,
        lock_updates={"registration_repository": "https://example.invalid/other.git"},
    )
    with pytest.raises(EvidenceIntegrityError, match="registration repository"):
        MEASUREMENT_SCRIPT.main(
            [
                "--source-root",
                str(source_root),
                "--fixture-dir",
                str(FIXTURE_DIR),
                "--lock",
                str(lock),
                "--preregistration",
                str(preregistration),
                "--output-dir",
                str(tmp_path / "output"),
            ]
        )


def test_builder_without_live_receipt_writes_pending_verdict_free_evidence(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
):
    bundle_dir = tmp_path / "bundle"
    integrity = bundle_dir / "integrity-report.json"
    evidence = bundle_dir / "evidence-record.json"
    exit_code = BUILDER_SCRIPT.main(
        _builder_args(
            efficacy_bundle,
            bundle_dir=bundle_dir,
        )
    )
    assert exit_code == 1
    integrity_data = _read(integrity)
    evidence_data = _read(evidence)
    assert integrity_data["unresolved_evidence_integrity_gaps"] == 1
    assert evidence_data["schema"] == "lakato-evidence-record/v1"
    assert "verdict" not in evidence_data
    assert evidence_data["measurement"]["value"] == 1
    gap = next(
        row
        for row in integrity_data["observations"]
        if row["gap_id"] == "deepeval_artifact_asserted_in_ci"
    )
    assert gap["resolved"] is False
    assert gap["evidence"]["actions"] == {"status": "wired_pending_completed_run"}
    expected_names = {
        "integrity-report.json",
        "evidence-record.json",
        "measurement-lock.json",
        "preregistration.json",
        "measurement-sequence.json",
        "deepeval-candidate.json",
        "deepeval-injected-mismatch.json",
        "tier0-positive.json",
        "tier0-positive.xml",
        "tier0-positive.md",
        "tier0-negative.json",
        "tier0-negative.xml",
        "tier0-negative.md",
        "tier0-restored.json",
        "tier0-restored.xml",
        "tier0-restored.md",
    }
    assert {path.name for path in bundle_dir.iterdir()} == expected_names


def test_live_actions_receipt_validator_accepts_exact_api_projection(
    efficacy_bundle: EfficacyBundle,
):
    receipt = _ci_receipt(efficacy_bundle.head)
    validated = BUILDER_SCRIPT._validate_ci_receipt(
        receipt,
        expected_head=efficacy_bundle.head,
        fetcher=_github_fetcher(receipt),
    )
    assert validated["run_id"] == receipt["run_id"]
    assert validated["artifact_digests"] == {
        "deepeval-heldout-v2": f"sha256:{'b' * 64}",
        "tier0-arrival-benchmark": f"sha256:{'a' * 64}",
    }


def test_live_actions_receipt_rejects_locally_forged_but_well_formed_digest(
    efficacy_bundle: EfficacyBundle,
):
    live = _ci_receipt(efficacy_bundle.head)
    forged = copy.deepcopy(live)
    forged["artifacts"][0]["digest"] = f"sha256:{'c' * 64}"
    with pytest.raises(EvidenceIntegrityError, match="does not exactly match live"):
        BUILDER_SCRIPT._validate_ci_receipt(
            forged,
            expected_head=efficacy_bundle.head,
            fetcher=_github_fetcher(live),
        )


@pytest.mark.parametrize("tamper", ["head", "step", "digest"])
def test_builder_rejects_invalid_completed_actions_receipt(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
    tamper: str,
):
    receipt_data = _ci_receipt(efficacy_bundle.head)
    if tamper == "head":
        receipt_data["head_sha"] = "f" * 40
    elif tamper == "step":
        receipt_data["jobs"][0]["steps"][0]["name"] = "Upload artifact only"
    else:
        receipt_data["artifacts"][0]["digest"] = "sha256:not-a-digest"
    with pytest.raises(EvidenceIntegrityError):
        BUILDER_SCRIPT._validate_ci_receipt(
            receipt_data,
            expected_head=efficacy_bundle.head,
            fetcher=_github_fetcher(receipt_data),
        )


@pytest.mark.parametrize("mode", ["dirty", "head", "sequence-lock-hash"])
def test_builder_rejects_candidate_and_measurement_lock_binding_drift(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
    mode: str,
):
    source_root = _copy_candidate(efficacy_bundle, tmp_path)
    measurement_dir = _copy_measurement(efficacy_bundle, tmp_path)
    if mode == "dirty":
        (source_root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    elif mode == "head":
        (source_root / "new-head.txt").write_text("new head\n", encoding="utf-8")
        _commit(source_root, "advance candidate beyond lock")
    else:
        sequence_path = measurement_dir / "measurement-sequence.json"
        sequence = _read(sequence_path)
        sequence["measurement_lock_sha256"] = "0" * 64
        _write(sequence_path, sequence)

    with pytest.raises(SystemExit):
        BUILDER_SCRIPT.main(
            _builder_args(
                efficacy_bundle,
                source_root=source_root,
                measurement_dir=measurement_dir,
                bundle_dir=tmp_path / "bundle",
            )
        )


@pytest.mark.parametrize("locked_preregistration", ["0" * 64, 123])
def test_builder_requires_lock_to_bind_the_actual_preregistration(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
    locked_preregistration: object,
):
    measurement_dir = _copy_measurement(efficacy_bundle, tmp_path)
    lock, preregistration = _published_governance_branch(
        efficacy_bundle,
        tmp_path,
        lock_updates={"preregistration_sha256": locked_preregistration},
    )
    sequence_path = measurement_dir / "measurement-sequence.json"
    sequence = _read(sequence_path)
    sequence["measurement_lock_sha256"] = _sha256(lock)
    _write(sequence_path, sequence)

    with pytest.raises((SystemExit, EvidenceIntegrityError)):
        BUILDER_SCRIPT.main(
            _builder_args(
                efficacy_bundle,
                measurement_dir=measurement_dir,
                lock=lock,
                preregistration=preregistration,
                bundle_dir=tmp_path / "bundle",
            )
        )


@pytest.mark.parametrize("kind", ["json", "junit", "markdown"])
def test_builder_rejects_noncanonical_json_junit_and_markdown_projection(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
    kind: str,
):
    measurement_dir = _copy_measurement(efficacy_bundle, tmp_path)
    sequence_path = measurement_dir / "measurement-sequence.json"
    sequence = _read(sequence_path)
    meta = sequence["measurements"][0]["artifacts"][kind]
    artifact = measurement_dir / meta["file"]
    if kind == "json":
        artifact.write_text(
            json.dumps(_read(artifact), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    elif kind == "junit":
        artifact.write_text(
            artifact.read_text(encoding="utf-8") + "\n<!-- noncanonical -->\n",
            encoding="utf-8",
        )
    else:
        artifact.write_text(
            artifact.read_text(encoding="utf-8") + "\nnoncanonical projection\n",
            encoding="utf-8",
        )
    meta["sha256"] = _sha256(artifact)
    _write(sequence_path, sequence)

    with pytest.raises(SystemExit, match="canonical|projection"):
        BUILDER_SCRIPT.main(
            _builder_args(
                efficacy_bundle,
                measurement_dir=measurement_dir,
                integrity_output=tmp_path / "integrity.json",
                evidence_output=tmp_path / "evidence.json",
            )
        )


def test_builder_rejects_artifact_path_escape(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
):
    measurement_dir = _copy_measurement(efficacy_bundle, tmp_path)
    sequence_path = measurement_dir / "measurement-sequence.json"
    sequence = _read(sequence_path)
    sequence["measurements"][0]["artifacts"]["json"]["file"] = "../escape.json"
    _write(sequence_path, sequence)

    with pytest.raises(EvidenceIntegrityError, match="escapes"):
        BUILDER_SCRIPT.main(
            _builder_args(
                efficacy_bundle,
                measurement_dir=measurement_dir,
                integrity_output=tmp_path / "integrity.json",
                evidence_output=tmp_path / "evidence.json",
            )
        )


def test_builder_rejects_non_strict_positive_negative_restored_chronology(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
):
    measurement_dir = _copy_measurement(efficacy_bundle, tmp_path)
    sequence_path = measurement_dir / "measurement-sequence.json"
    sequence = _read(sequence_path)
    sequence["measurements"][1]["measured_at"] = sequence["measurements"][0]["measured_at"]
    _write(sequence_path, sequence)

    with pytest.raises(EvidenceIntegrityError, match="chronology"):
        BUILDER_SCRIPT.main(
            _builder_args(
                efficacy_bundle,
                measurement_dir=measurement_dir,
                integrity_output=tmp_path / "integrity.json",
                evidence_output=tmp_path / "evidence.json",
            )
        )


def test_builder_rejects_deepeval_measured_before_restored_positive(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
):
    measurement_dir = _copy_measurement(efficacy_bundle, tmp_path)
    sequence_path = measurement_dir / "measurement-sequence.json"
    sequence = _read(sequence_path)
    early = "2026-07-23T00:03:30Z"
    sequence["deepeval"]["measured_at"] = early
    for key in ("candidate", "injected_mismatch"):
        meta = sequence["deepeval"][key]
        path = measurement_dir / meta["file"]
        record = _read(path)
        record["measured_at"] = early
        _write(path, record)
        meta["sha256"] = _sha256(path)
    _write(sequence_path, sequence)

    with pytest.raises((SystemExit, EvidenceIntegrityError), match="chronology"):
        BUILDER_SCRIPT.main(
            _builder_args(
                efficacy_bundle,
                measurement_dir=measurement_dir,
                integrity_output=tmp_path / "integrity.json",
                evidence_output=tmp_path / "evidence.json",
            )
        )


def test_builder_rejects_deepeval_sequence_timestamp_drift(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
):
    measurement_dir = _copy_measurement(efficacy_bundle, tmp_path)
    sequence_path = measurement_dir / "measurement-sequence.json"
    sequence = _read(sequence_path)
    sequence["deepeval"]["measured_at"] = "2026-07-23T00:06:00Z"
    _write(sequence_path, sequence)

    with pytest.raises((SystemExit, EvidenceIntegrityError), match="timestamp|measured_at"):
        BUILDER_SCRIPT.main(
            _builder_args(
                efficacy_bundle,
                measurement_dir=measurement_dir,
                bundle_dir=tmp_path / "bundle",
            )
        )


@pytest.mark.parametrize("tamper", ["wrong-head", "extra-delta"])
def test_builder_requires_exact_safe_observed_success_deepeval_negative(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
    tamper: str,
):
    measurement_dir = _copy_measurement(efficacy_bundle, tmp_path)
    sequence_path = measurement_dir / "measurement-sequence.json"
    sequence = _read(sequence_path)
    meta = sequence["deepeval"]["injected_mismatch"]
    path = measurement_dir / meta["file"]
    record = _read(path)
    if tamper == "wrong-head":
        record["source"]["git_head"] = "f" * 40
    else:
        record["observations"][1]["reason"] = "unlocked extra mutation"
    _write(path, record)
    meta["sha256"] = _sha256(path)
    _write(sequence_path, sequence)

    with pytest.raises((SystemExit, EvidenceIntegrityError)):
        BUILDER_SCRIPT.main(
            _builder_args(
                efficacy_bundle,
                measurement_dir=measurement_dir,
                integrity_output=tmp_path / "integrity.json",
                evidence_output=tmp_path / "evidence.json",
            )
        )


def test_builder_recomputes_and_checks_sequence_deepeval_metrics(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
):
    measurement_dir = _copy_measurement(efficacy_bundle, tmp_path)
    sequence_path = measurement_dir / "measurement-sequence.json"
    sequence = _read(sequence_path)
    sequence["deepeval"]["computed_metrics"]["cases_total"] = 99
    _write(sequence_path, sequence)

    with pytest.raises((SystemExit, EvidenceIntegrityError)):
        BUILDER_SCRIPT.main(
            _builder_args(
                efficacy_bundle,
                measurement_dir=measurement_dir,
                integrity_output=tmp_path / "integrity.json",
                evidence_output=tmp_path / "evidence.json",
            )
        )


@pytest.mark.parametrize(
    "target",
    ["alias", "tier0-positive", "measurement-sequence", "source-tree"],
)
def test_builder_rejects_aliasing_or_in_tree_outputs(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
    target: str,
):
    source_root = _copy_candidate(efficacy_bundle, tmp_path)
    measurement_dir = _copy_measurement(efficacy_bundle, tmp_path)
    bundle_dir = tmp_path / "bundle"
    integrity = bundle_dir / "integrity-report.json"
    evidence = bundle_dir / "evidence-record.json"
    if target == "alias":
        evidence = integrity
    elif target == "tier0-positive":
        integrity = measurement_dir / "tier0-positive.json"
    elif target == "measurement-sequence":
        evidence = measurement_dir / "measurement-sequence.json"
    else:
        bundle_dir = source_root / "bundle"

    with pytest.raises((SystemExit, EvidenceIntegrityError)):
        BUILDER_SCRIPT.main(
            _builder_args(
                efficacy_bundle,
                source_root=source_root,
                measurement_dir=measurement_dir,
                bundle_dir=bundle_dir,
                integrity_output=integrity,
                evidence_output=evidence,
                output_aliases=True,
            )
        )


def _rebind_measurement_to_head(
    *,
    measurement_dir: Path,
    lock_path: Path,
    preregistration_path: Path,
    head: str,
) -> None:
    sequence_path = measurement_dir / "measurement-sequence.json"
    sequence = _read(sequence_path)
    sequence["source"] = {"git_head": head, "dirty": False}
    sequence["measurement_lock_sha256"] = _sha256(lock_path)
    sequence["prospective_registration"] = prospective_git_receipt(
        lock_path,
        preregistration_path,
    )
    for key in ("candidate", "injected_mismatch"):
        meta = sequence["deepeval"][key]
        path = measurement_dir / meta["file"]
        record = _read(path)
        record["source"]["git_head"] = head
        _write(path, record)
        meta["sha256"] = _sha256(path)
    _write(sequence_path, sequence)


def test_builder_returns_one_when_an_integrity_gap_remains(
    efficacy_bundle: EfficacyBundle,
    tmp_path: Path,
):
    source_root = _copy_candidate(efficacy_bundle, tmp_path)
    workflow = source_root / ".github" / "workflows" / "ci.yml"
    workflow.write_text("name: CI without DeepEval assertion\n", encoding="utf-8")
    head = _commit(source_root, "remove DeepEval CI assertion")
    measurement_dir = _copy_measurement(efficacy_bundle, tmp_path)
    lock, preregistration = _published_governance_branch(
        efficacy_bundle,
        tmp_path,
        lock_updates={"candidate_git_head": head},
    )
    _rebind_measurement_to_head(
        measurement_dir=measurement_dir,
        lock_path=lock,
        preregistration_path=preregistration,
        head=head,
    )

    bundle_dir = tmp_path / "bundle"
    integrity = bundle_dir / "integrity-report.json"
    evidence = bundle_dir / "evidence-record.json"
    exit_code = BUILDER_SCRIPT.main(
        _builder_args(
            efficacy_bundle,
            source_root=source_root,
            measurement_dir=measurement_dir,
            lock=lock,
            preregistration=preregistration,
            bundle_dir=bundle_dir,
        )
    )
    assert exit_code == 1
    assert _read(integrity)["unresolved_evidence_integrity_gaps"] == 1
    assert _read(evidence)["measurement"]["value"] == 1
