#!/usr/bin/env python3
"""Fail-closed readiness harness for OOPTDD and the generic Ouroboros kernel."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = Path("docs/architecture/ooptdd-ouroboros-readiness-contract.json")
SCHEMA_VERSION = "ooptdd-ouroboros-readiness/v1"
STATUSES = ("PASS", "UNMET", "BLOCKED")
EXECUTABLE_EVIDENCE = frozenset({"python", "test"})


class ContractError(ValueError):
    """The readiness authority is malformed or internally inconsistent."""


@dataclass(frozen=True, order=True)
class Violation:
    rule_id: str
    path: str
    symbol: str
    message: str
    remediation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "message": self.message,
            "path": self.path,
            "remediation": self.remediation,
            "rule_id": self.rule_id,
            "symbol": self.symbol,
        }


@dataclass(frozen=True)
class ProfileReport:
    profile_id: str
    target: str
    status: str
    passed: int
    total: int
    blockers: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "blockers": list(self.blockers),
            "passed": self.passed,
            "profile": self.profile_id,
            "status": self.status,
            "target": self.target,
            "total": self.total,
        }


def _exact_fields(
    value: object, required: set[str], context: str, *, optional: set[str] | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{context} must be an object")
    optional = optional or set()
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing or unknown:
        raise ContractError(f"{context} fields are invalid; missing={missing}, unknown={unknown}")
    return value


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ContractError(f"{context} must be non-empty text without NUL")
    return value


def _text_list(value: object, context: str, *, nonempty: bool = False) -> list[str]:
    if (
        not isinstance(value, list)
        or (nonempty and not value)
        or not all(isinstance(item, str) and item.strip() and "\0" not in item for item in value)
        or len(value) != len(set(value))
    ):
        raise ContractError(f"{context} must contain unique non-empty strings")
    return value


def _relative_path(value: object, context: str) -> str:
    text = _text(value, context)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ContractError(f"{context} must be repository-relative without '..'")
    return text


def _validate_evidence(value: object, capability_id: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ContractError(f"capability {capability_id} evidence must be an array")
    evidence: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        item = _exact_fields(
            item,
            {"id", "kind", "path", "symbol", "claim"},
            f"capability {capability_id} evidence[{index}]",
        )
        _text(item["id"], "evidence id")
        if item["kind"] not in {"python", "test", "document"}:
            raise ContractError(f"evidence {item['id']} has unsupported kind")
        _relative_path(item["path"], f"evidence {item['id']} path")
        _text(item["symbol"], f"evidence {item['id']} symbol")
        _text(item["claim"], f"evidence {item['id']} claim")
        evidence.append(item)
    return evidence


def _validate_capabilities(value: object, evidence_classes: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ContractError("capabilities must be a non-empty array")
    capabilities: list[dict[str, Any]] = []
    evidence_ids: list[str] = []
    for index, item in enumerate(value):
        item = _exact_fields(
            item,
            {
                "id",
                "title",
                "area",
                "severity",
                "status",
                "enforcement",
                "depends_on",
                "evidence",
                "falsifier",
                "solution",
                "acceptance",
            },
            f"capabilities[{index}]",
        )
        capability_id = _text(item["id"], "capability id")
        if re.fullmatch(r"[A-Z][A-Z0-9-]+", capability_id) is None:
            raise ContractError(f"invalid capability id {capability_id!r}")
        _text(item["title"], f"capability {capability_id} title")
        _text(item["area"], f"capability {capability_id} area")
        if item["severity"] not in {"critical", "high", "medium"}:
            raise ContractError(f"capability {capability_id} has invalid severity")
        if item["status"] not in STATUSES:
            raise ContractError(f"capability {capability_id} has invalid status")
        if item["enforcement"] not in evidence_classes:
            raise ContractError(f"capability {capability_id} has unknown enforcement class")
        _text_list(item["depends_on"], f"capability {capability_id} dependencies")
        item["evidence"] = _validate_evidence(item["evidence"], capability_id)
        evidence_ids.extend(evidence["id"] for evidence in item["evidence"])
        _text(item["falsifier"], f"capability {capability_id} falsifier")
        _text(item["solution"], f"capability {capability_id} solution")
        _text_list(item["acceptance"], f"capability {capability_id} acceptance", nonempty=True)
        if item["status"] == "PASS" and not any(
            evidence["kind"] in EXECUTABLE_EVIDENCE for evidence in item["evidence"]
        ):
            raise ContractError(
                f"PASS capability {capability_id} needs executable python or test evidence"
            )
        if item["status"] == "PASS" and item["enforcement"] in {
            "declaration_only",
            "external_required",
        }:
            raise ContractError(
                f"PASS capability {capability_id} cannot use {item['enforcement']} evidence"
            )
        capabilities.append(item)
    ids = [item["id"] for item in capabilities]
    if len(ids) != len(set(ids)):
        raise ContractError("capability ids must be unique")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ContractError("evidence ids must be globally unique")
    return capabilities


def _validate_dependency_graph(capabilities: list[dict[str, Any]]) -> None:
    by_id = {item["id"]: item for item in capabilities}
    for item in capabilities:
        unknown = set(item["depends_on"]) - set(by_id)
        if unknown:
            raise ContractError(
                f"capability {item['id']} has unknown dependencies {sorted(unknown)}"
            )
        if item["id"] in item["depends_on"]:
            raise ContractError(f"capability {item['id']} cannot depend on itself")
        if item["status"] == "BLOCKED" and not any(
            by_id[item_id]["status"] != "PASS" for item_id in item["depends_on"]
        ):
            raise ContractError(f"BLOCKED capability {item['id']} needs a non-PASS dependency")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(capability_id: str) -> None:
        if capability_id in visiting:
            raise ContractError(f"capability dependency cycle includes {capability_id}")
        if capability_id in visited:
            return
        visiting.add(capability_id)
        for dependency in by_id[capability_id]["depends_on"]:
            visit(dependency)
        visiting.remove(capability_id)
        visited.add(capability_id)

    for capability_id in by_id:
        visit(capability_id)


def _validate_profiles(value: object, capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ContractError("profiles must be a non-empty array")
    by_id = {item["id"]: item for item in capabilities}
    profiles: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        item = _exact_fields(
            item,
            {"id", "target", "declared_status", "required_capabilities"},
            f"profiles[{index}]",
        )
        profile_id = _text(item["id"], "profile id")
        if re.fullmatch(r"[a-z][a-z0-9-]+", profile_id) is None:
            raise ContractError(f"invalid profile id {profile_id!r}")
        _text(item["target"], f"profile {profile_id} target")
        required = _text_list(
            item["required_capabilities"],
            f"profile {profile_id} required capabilities",
            nonempty=True,
        )
        unknown = set(required) - set(by_id)
        if unknown:
            raise ContractError(
                f"profile {profile_id} names unknown capabilities {sorted(unknown)}"
            )
        required_set = set(required)
        missing_dependencies = {
            dependency
            for capability_id in required
            for dependency in by_id[capability_id]["depends_on"]
            if dependency not in required_set
        }
        if missing_dependencies:
            raise ContractError(
                f"profile {profile_id} omits dependencies {sorted(missing_dependencies)}"
            )
        expected = (
            "READY"
            if all(by_id[item_id]["status"] == "PASS" for item_id in required)
            else "NOT_READY"
        )
        if item["declared_status"] != expected:
            raise ContractError(
                f"profile {profile_id} declared_status must be derived as {expected}"
            )
        profiles.append(item)
    profile_ids = [item["id"] for item in profiles]
    if len(profile_ids) != len(set(profile_ids)):
        raise ContractError("profile ids must be unique")
    referenced = {item for profile in profiles for item in profile["required_capabilities"]}
    orphaned = set(by_id) - referenced
    if orphaned:
        raise ContractError(f"capabilities must belong to a profile; orphaned={sorted(orphaned)}")
    return profiles


def _validate_remediation(value: object, capabilities: list[dict[str, Any]]) -> None:
    if not isinstance(value, list):
        raise ContractError("remediation_sequence must be an array")
    by_id = {item["id"]: item for item in capabilities}
    seen: list[str] = []
    order_by_id: dict[str, int] = {}
    for index, item in enumerate(value, start=1):
        item = _exact_fields(
            item,
            {"order", "capabilities", "exit"},
            f"remediation_sequence[{index - 1}]",
        )
        if isinstance(item["order"], bool) or item["order"] != index:
            raise ContractError("remediation order must be contiguous and start at one")
        ids = _text_list(
            item["capabilities"], f"remediation step {index} capabilities", nonempty=True
        )
        unknown = set(ids) - set(by_id)
        if unknown:
            raise ContractError(
                f"remediation step {index} has unknown capabilities {sorted(unknown)}"
            )
        if any(by_id[item_id]["status"] == "PASS" for item_id in ids):
            raise ContractError("remediation steps may contain only non-PASS capabilities")
        _text(item["exit"], f"remediation step {index} exit")
        seen.extend(ids)
        order_by_id.update((item_id, index) for item_id in ids)
    expected = {item["id"] for item in capabilities if item["status"] != "PASS"}
    if len(seen) != len(set(seen)) or set(seen) != expected:
        missing = sorted(expected - set(seen))
        raise ContractError(
            f"remediation must cover every non-PASS capability once; missing={missing}"
        )
    for item_id, order in order_by_id.items():
        later_dependencies = [
            dependency
            for dependency in by_id[item_id]["depends_on"]
            if dependency in order_by_id and order_by_id[dependency] > order
        ]
        if later_dependencies:
            raise ContractError(
                f"remediation for {item_id} precedes dependencies {sorted(later_dependencies)}"
            )


def load_contract(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read contract {path}: {error}") from error
    value = _exact_fields(
        value,
        {
            "schema_version",
            "harness_tier",
            "target_tier",
            "claim",
            "catalog_document",
            "status_vocabulary",
            "evidence_classes",
            "profiles",
            "capabilities",
            "remediation_sequence",
        },
        "contract",
    )
    if value["schema_version"] != SCHEMA_VERSION:
        raise ContractError("unsupported readiness contract schema_version")
    if value["harness_tier"] != "L_IDE" or value["target_tier"] != "L_RT":
        raise ContractError("readiness harness must declare L_IDE -> L_RT")
    _text(value["claim"], "claim")
    _relative_path(value["catalog_document"], "catalog_document")
    if value["status_vocabulary"] != list(STATUSES):
        raise ContractError(f"status_vocabulary must be exactly {list(STATUSES)}")
    classes = _exact_fields(
        value["evidence_classes"],
        {"ci_now", "runtime_required", "external_required", "declaration_only"},
        "evidence_classes",
    )
    for key, description in classes.items():
        _text(description, f"evidence class {key}")
    capabilities = _validate_capabilities(value["capabilities"], set(classes))
    _validate_dependency_graph(capabilities)
    value["profiles"] = _validate_profiles(value["profiles"], capabilities)
    _validate_remediation(value["remediation_sequence"], capabilities)
    value["capabilities"] = capabilities
    return value


def _symbol_exists(path: Path, symbol: str) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    return any(getattr(node, "name", None) == symbol for node in ast.walk(tree))


def check_repository(repo_root: Path, contract: dict[str, Any]) -> tuple[Violation, ...]:
    violations: list[Violation] = []
    catalog_path = repo_root / contract["catalog_document"]
    try:
        catalog = catalog_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        catalog = ""
        violations.append(
            Violation(
                "ORH001",
                contract["catalog_document"],
                "catalog_document",
                "readiness catalog cannot be read",
                "Restore the reviewed human-readable capability catalog.",
            )
        )
    for capability in contract["capabilities"]:
        if capability["id"] not in catalog:
            violations.append(
                Violation(
                    "ORH002",
                    contract["catalog_document"],
                    capability["id"],
                    "capability is absent from the human-readable solution catalog",
                    "Document the gap, boundary, solution, and acceptance evidence.",
                )
            )
        for evidence in capability["evidence"]:
            evidence_path = repo_root / evidence["path"]
            if not evidence_path.is_file():
                violations.append(
                    Violation(
                        "ORH003",
                        evidence["path"],
                        evidence["symbol"],
                        f"evidence for {capability['id']} does not exist",
                        "Restore the evidence or change the capability status.",
                    )
                )
                continue
            if evidence["kind"] in EXECUTABLE_EVIDENCE:
                exists = _symbol_exists(evidence_path, evidence["symbol"])
            else:
                try:
                    exists = evidence["symbol"] in evidence_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    exists = False
            if not exists:
                violations.append(
                    Violation(
                        "ORH004",
                        evidence["path"],
                        evidence["symbol"],
                        f"evidence symbol for {capability['id']} is missing",
                        "Restore executable evidence or demote the capability from PASS.",
                    )
                )
    return tuple(sorted(violations))


def evaluate_profile(
    contract: dict[str, Any], profile_id: str, *, evidence_intact: bool = True
) -> ProfileReport:
    profile = next((item for item in contract["profiles"] if item["id"] == profile_id), None)
    if profile is None:
        raise ContractError(f"unknown readiness profile {profile_id!r}")
    by_id = {item["id"]: item for item in contract["capabilities"]}
    required = [by_id[item_id] for item_id in profile["required_capabilities"]]
    blockers = tuple(
        {
            "id": item["id"],
            "severity": item["severity"],
            "solution": item["solution"],
            "status": item["status"],
            "title": item["title"],
        }
        for item in required
        if item["status"] != "PASS"
    )
    return ProfileReport(
        profile["id"],
        profile["target"],
        "READY" if not blockers and evidence_intact else "NOT_READY",
        len(required) - len(blockers),
        len(required),
        blockers,
    )


def _render_text(report: ProfileReport, violations: tuple[Violation, ...]) -> str:
    lines = [
        f"ooptdd-ouroboros readiness: {report.profile_id} {report.status} "
        f"({report.passed}/{report.total} PASS)"
    ]
    for violation in violations:
        lines.append(
            f"{violation.rule_id} {violation.path} {violation.symbol}: {violation.message}"
        )
    for blocker in report.blockers:
        lines.append(f"[{blocker['status']}] {blocker['id']} {blocker['title']}")
        lines.append(f"  solution: {blocker['solution']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--profile", default="core")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    contract_path = args.contract if args.contract.is_absolute() else repo_root / args.contract
    try:
        contract = load_contract(contract_path)
        violations = check_repository(repo_root, contract)
        report = evaluate_profile(contract, args.profile, evidence_intact=not violations)
    except ContractError as error:
        if args.format == "json":
            print(json.dumps({"error": str(error), "status": "INVALID"}, sort_keys=True))
        else:
            print(f"ooptdd-ouroboros readiness: INVALID: {error}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(
            json.dumps(
                {**report.to_dict(), "violations": [item.to_dict() for item in violations]},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(_render_text(report, violations))
    return 0 if report.status == "READY" and not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
