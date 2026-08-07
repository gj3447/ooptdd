"""Canaries for the OOPTDD/Ouroboros readiness and promotion contract."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_ooptdd_ouroboros_readiness.py"
CONTRACT = ROOT / "docs" / "architecture" / "ooptdd-ouroboros-readiness-contract.json"


def _load_checker():
    spec = importlib.util.spec_from_file_location("ooptdd_ouroboros_readiness", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = _load_checker()


def _raw_contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _write_contract(tmp_path: Path, value: dict) -> Path:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _capability(value: dict, capability_id: str) -> dict:
    return next(item for item in value["capabilities"] if item["id"] == capability_id)


def _profile(value: dict, profile_id: str) -> dict:
    return next(item for item in value["profiles"] if item["id"] == profile_id)


def test_core_profile_is_ready_and_other_profiles_are_honestly_not_ready():
    contract = CHECKER.load_contract(CONTRACT)
    assert CHECKER.check_repository(ROOT, contract) == ()

    core = CHECKER.evaluate_profile(contract, "core")
    assert core.status == "READY" and core.passed == core.total == 6

    maintainability = CHECKER.evaluate_profile(contract, "maintainability")
    runtime = CHECKER.evaluate_profile(contract, "runtime")
    authority = CHECKER.evaluate_profile(contract, "authority")
    assert maintainability.status == runtime.status == authority.status == "NOT_READY"
    assert {item["id"] for item in maintainability.blockers} == {
        "FRAMEWORK-EXTENSIONS-010",
        "FRAMEWORK-KERNEL-CONVERGENCE-011",
    }
    assert "RUNTIME-FAULT-MATRIX-140" in {item["id"] for item in runtime.blockers}
    assert {item["id"] for item in authority.blockers} == {"AUTH-ENVELOPE-200"}


def test_contract_rejects_unknown_fields_and_status_vocabulary_drift(tmp_path):
    contract = _raw_contract()
    contract["future_switch"] = True
    with pytest.raises(CHECKER.ContractError, match="unknown=.*future_switch"):
        CHECKER.load_contract(_write_contract(tmp_path, contract))

    contract = _raw_contract()
    contract["status_vocabulary"].append("ASSUMED")
    with pytest.raises(CHECKER.ContractError, match="status_vocabulary"):
        CHECKER.load_contract(_write_contract(tmp_path, contract))


def test_pass_cannot_be_supported_by_declaration_only_or_documentation(tmp_path):
    contract = _raw_contract()
    capability = _capability(contract, "KERNEL-REDUCER-001")
    capability["enforcement"] = "declaration_only"
    capability["evidence"] = [
        {
            "id": "EV-DOCUMENT-ONLY",
            "kind": "document",
            "path": "README.md",
            "symbol": "ooptdd",
            "claim": "Documentation is not executable proof.",
        }
    ]
    with pytest.raises(CHECKER.ContractError, match="needs executable"):
        CHECKER.load_contract(_write_contract(tmp_path, contract))


def test_profile_status_is_derived_instead_of_trusted(tmp_path):
    contract = _raw_contract()
    _profile(contract, "runtime")["declared_status"] = "READY"
    with pytest.raises(CHECKER.ContractError, match="derived as NOT_READY"):
        CHECKER.load_contract(_write_contract(tmp_path, contract))


def test_dependency_cycles_and_incomplete_remediation_fail_closed(tmp_path):
    contract = _raw_contract()
    _capability(contract, "RUNTIME-LINEAGE-020")["depends_on"].append(
        "RUNTIME-REPLAY-EVIDENCE-030"
    )
    with pytest.raises(CHECKER.ContractError, match="dependency cycle"):
        CHECKER.load_contract(_write_contract(tmp_path, contract))

    contract = _raw_contract()
    contract["remediation_sequence"][0]["capabilities"].remove("FRAMEWORK-EXTENSIONS-010")
    with pytest.raises(CHECKER.ContractError, match="cover every non-PASS capability"):
        CHECKER.load_contract(_write_contract(tmp_path, contract))


def test_missing_executable_evidence_symbol_is_a_violation():
    contract = CHECKER.load_contract(CONTRACT)
    _capability(contract, "KERNEL-REDUCER-001")["evidence"][0]["symbol"] = "removed_test"
    violations = CHECKER.check_repository(ROOT, contract)
    assert any(item.rule_id == "ORH004" and item.symbol == "removed_test" for item in violations)


def test_evidence_drift_demotes_the_effective_cli_status(tmp_path):
    contract = _raw_contract()
    _capability(contract, "KERNEL-REDUCER-001")["evidence"][0]["symbol"] = "removed_test"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo-root",
            str(ROOT),
            "--contract",
            str(_write_contract(tmp_path, contract)),
            "--format",
            "json",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["status"] == "NOT_READY"
    assert any(item["symbol"] == "removed_test" for item in report["violations"])


def test_release_publish_is_gated_by_the_reusable_ci_workflow():
    ci = yaml.load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    release = yaml.load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )

    assert "workflow_call" in ci["on"]
    assert release["jobs"]["validate"] == {
        "needs": "route",
        "permissions": {"contents": "read"},
        "uses": "./.github/workflows/ci.yml",
    }
    assert set(release["jobs"]["build"]["needs"]) == {"route", "validate"}
    assert set(release["jobs"]["publish"]["needs"]) == {"route", "validate", "build"}


def test_checker_exit_codes_distinguish_ready_not_ready_and_invalid():
    ready = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert ready.returncode == 0 and "core READY" in ready.stdout

    not_ready = subprocess.run(
        [sys.executable, str(SCRIPT), "--profile", "runtime", "--format", "json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(not_ready.stdout)
    assert not_ready.returncode == 1 and report["status"] == "NOT_READY"
    assert any(item["id"] == "RUNTIME-JOURNAL-060" for item in report["blockers"])

    invalid = subprocess.run(
        [sys.executable, str(SCRIPT), "--profile", "unknown"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 2 and "unknown readiness profile" in invalid.stderr
