"""I/O-free authenticated-gate completion assessment.

This policy is intentionally narrower than authoritative receipt or lineage validation.  It
proves that caller-selected receipt roles bind distinct artifacts satisfying explicit contracts
and records the verdict returned by a caller-selected authority port over the exact canonical
bytes.  I/O lives in ``completion_io``; this module only evaluates supplied immutable values.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ooptdd.identity import CANONICALIZATION, Digest, canonical_json_bytes, digest_json

from .gate_adapter import GATE_EVIDENCE_VERSION, validate_gate_evidence
from .model import (
    CycleIdentity,
    EvidenceTier,
    MonitorVerdict,
    ObservationVerdict,
    OracleBoundary,
    RunEvidence,
    RunOutcome,
    RunRole,
)
from .schema import validate_receipt


def _nonempty_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{label} must be non-empty text without NUL")
    return value


@dataclass(frozen=True)
class CompletionRoleContract:
    """Artifact type required for one completion-bearing run role."""

    role: RunRole
    artifact_scope: str
    artifact_canonicalization: str
    artifact_schema_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, RunRole):
            raise TypeError("completion role must be a RunRole")
        _nonempty_text(self.artifact_scope, "artifact_scope")
        _nonempty_text(self.artifact_canonicalization, "artifact_canonicalization")
        _nonempty_text(self.artifact_schema_version, "artifact_schema_version")

    def accepts(self, artifact: Digest) -> bool:
        return (
            artifact.scope == self.artifact_scope
            and artifact.canonicalization == self.artifact_canonicalization
            and artifact.schema_version == self.artifact_schema_version
        )


@dataclass(frozen=True)
class AuthenticatedGateCompletionPolicy:
    """Trust policy for exact-byte gate evidence.

    The injected verifier is selected by the caller.  ``expected_authority_id`` checks the
    identity *claimed by that verifier*; selecting and authenticating the verifier itself is a
    deployment trust-root obligation, not something this value can establish cryptographically.
    """

    name: str
    roles: tuple[CompletionRoleContract, ...]
    expected_authority_id: str | None
    require_authentication: bool
    require_independence: bool

    def __post_init__(self) -> None:
        _nonempty_text(self.name, "completion policy name")
        if not isinstance(self.roles, tuple) or any(
            not isinstance(role, CompletionRoleContract) for role in self.roles
        ):
            raise TypeError("roles must be a tuple of CompletionRoleContract values")
        role_names = tuple(contract.role for contract in self.roles)
        if len(role_names) != len(set(role_names)):
            raise ValueError("completion policy roles must be unique")
        if self.expected_authority_id is not None:
            _nonempty_text(self.expected_authority_id, "expected_authority_id")
        if not isinstance(self.require_authentication, bool):
            raise TypeError("require_authentication must be a boolean")
        if not isinstance(self.require_independence, bool):
            raise TypeError("require_independence must be a boolean")


def ooptdd_mutation_v2_completion_policy(
    expected_authority_id: str,
) -> AuthenticatedGateCompletionPolicy:
    """Explicit preset reproducing the historical four-role gate policy."""

    roles = tuple(
        CompletionRoleContract(
            role=role,
            artifact_scope="ouroboros-gate-evidence",
            artifact_canonicalization=CANONICALIZATION,
            artifact_schema_version=GATE_EVIDENCE_VERSION,
        )
        for role in RunRole
    )
    return AuthenticatedGateCompletionPolicy(
        name="ooptdd_mutation_authenticated_gate_completion/v2",
        roles=roles,
        expected_authority_id=expected_authority_id,
        require_authentication=True,
        require_independence=True,
    )


@dataclass(frozen=True)
class AuthorityVerdict:
    """Out-of-band judgment, including the verifier's claimed identity, over exact bytes."""

    authenticated: bool
    authority_id: str
    independent: bool

    def __post_init__(self) -> None:
        if not isinstance(self.authenticated, bool) or not isinstance(self.independent, bool):
            raise TypeError("authority verdict flags must be booleans")
        _nonempty_text(self.authority_id, "authority_id")


@dataclass(frozen=True)
class ResolvedGateEvidence:
    """Immutable exact bytes plus their out-of-band authority verdict."""

    artifact: Digest
    artifact_bytes: bytes
    authority: AuthorityVerdict

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, Digest):
            raise TypeError("artifact must be a Digest")
        if type(self.artifact_bytes) is not bytes:
            raise TypeError("artifact_bytes must be exact bytes")
        if not isinstance(self.authority, AuthorityVerdict):
            raise TypeError("authority must be an AuthorityVerdict")


@dataclass(frozen=True)
class CompletionAssessment:
    """Consistent result from the local completion policy evaluator."""

    policy: AuthenticatedGateCompletionPolicy
    satisfied: bool
    errors: tuple[str, ...]
    validated_artifacts: tuple[Digest, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.policy, AuthenticatedGateCompletionPolicy):
            raise TypeError("policy must be an AuthenticatedGateCompletionPolicy")
        if not isinstance(self.satisfied, bool):
            raise TypeError("satisfied must be a boolean")
        if not isinstance(self.errors, tuple) or any(
            not isinstance(error, str) or not error for error in self.errors
        ):
            raise TypeError("errors must be a tuple of non-empty strings")
        if not isinstance(self.validated_artifacts, tuple) or any(
            not isinstance(artifact, Digest) for artifact in self.validated_artifacts
        ):
            raise TypeError("validated_artifacts must be a tuple of Digest values")
        if len(self.validated_artifacts) != len(set(self.validated_artifacts)):
            raise ValueError("validated_artifacts must be unique")
        if self.satisfied and self.errors:
            raise ValueError("a satisfied assessment cannot contain errors")
        if self.satisfied and len(self.validated_artifacts) != len(self.policy.roles):
            raise ValueError("a satisfied assessment requires one artifact per policy role")
        if self.satisfied and any(
            not contract.accepts(artifact)
            for contract, artifact in zip(self.policy.roles, self.validated_artifacts, strict=True)
        ):
            raise ValueError("a satisfied assessment requires policy-conforming artifacts")


@dataclass(frozen=True)
class _RunAssessment:
    """Private value returned by one context-bound gate-artifact assessment."""

    errors: tuple[str, ...]
    validated_artifact: Digest | None = None

    def __post_init__(self) -> None:
        if bool(self.errors) == (self.validated_artifact is not None):
            raise ValueError(
                "a run assessment must contain either errors or one validated artifact"
            )


@dataclass(frozen=True)
class _RunAssessmentContext:
    """Receipt-wide immutable inputs shared by every per-run assessment."""

    cycle: CycleIdentity
    oracle: OracleBoundary
    policy: AuthenticatedGateCompletionPolicy


def _cycle_from_receipt(receipt: Mapping[str, Any]) -> CycleIdentity:
    value = receipt["cycle"]
    if not isinstance(value, dict):
        raise ValueError("cycle must be an object")
    return CycleIdentity(
        cycle_id=value["cycle_id"],
        generation=value["generation"],
        previous_receipt_sha256=value["previous_receipt_sha256"],
    )


def _run_from_dict(value: Any) -> RunEvidence:
    if not isinstance(value, dict):
        raise ValueError("run must be an object")
    return RunEvidence(
        role=RunRole(value["role"]),
        run_id=value["run_id"],
        artifact_namespace=value["artifact_namespace"],
        outcome=RunOutcome(value["outcome"]),
        observation=ObservationVerdict(value["observation"]),
        monitor=MonitorVerdict(value["monitor"]),
        evidence_tier=EvidenceTier(value["evidence_tier"]),
        artifact=Digest.from_dict(value["artifact"]),
        material_lock_sha256=value["material_lock_sha256"],
        executed_source=Digest.from_dict(value["executed_source"]),
    )


def parse_and_validate_artifact_bytes(
    artifact: Digest,
    artifact_bytes: bytes,
    *,
    contract: CompletionRoleContract,
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Validate exact canonical bytes and their content address without external effects."""

    if not isinstance(artifact, Digest):
        return None, ("artifact must be a Digest",)
    if not isinstance(contract, CompletionRoleContract):
        return None, ("artifact contract must be a CompletionRoleContract",)
    if not contract.accepts(artifact):
        return None, ("artifact identity does not satisfy its policy contract",)
    if type(artifact_bytes) is not bytes:
        return None, ("artifact payload must be exact bytes",)
    try:
        value = json.loads(artifact_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, (f"artifact is not UTF-8 JSON: {error}",)
    if not isinstance(value, dict):
        return None, ("artifact root must be an object",)
    try:
        canonical = canonical_json_bytes(value)
    except (TypeError, ValueError) as error:
        return None, (f"artifact has no canonical encoding: {error}",)
    if canonical != artifact_bytes:
        return None, ("artifact bytes are not the canonical JSON encoding",)
    actual = digest_json(
        value,
        scope=contract.artifact_scope,
        schema_version=contract.artifact_schema_version,
    )
    if actual != artifact:
        return None, ("artifact bytes do not match the receipt artifact digest",)
    return value, ()


def required_gate_artifacts(
    receipt: Any,
    *,
    policy: AuthenticatedGateCompletionPolicy,
) -> tuple[tuple[Digest, ...], tuple[str, ...]]:
    """Extract policy-selected artifact identities from a locally valid receipt."""

    if not isinstance(policy, AuthenticatedGateCompletionPolicy):
        raise TypeError("policy must be an AuthenticatedGateCompletionPolicy")

    errors = tuple(f"receipt: {error}" for error in validate_receipt(receipt))
    if errors:
        return (), errors
    if not isinstance(receipt, dict):
        return (), ("receipt must be an object",)
    if receipt.get("status") != "complete":
        return (), ("receipt: authenticated gate completion requires status 'complete'",)
    try:
        runs = tuple(_run_from_dict(value) for value in receipt["runs"])
    except (KeyError, TypeError, ValueError) as error:
        return (), (f"receipt runs cannot be reconstructed: {error}",)
    runs_by_role = {run.role: run for run in runs}
    if len(runs_by_role) != len(runs):
        return (), ("receipt runs must have unique roles",)
    missing_roles = tuple(
        contract.role for contract in policy.roles if contract.role not in runs_by_role
    )
    if missing_roles:
        return (), (
            "receipt lacks policy roles: " + ", ".join(role.value for role in missing_roles),
        )
    selected = tuple(runs_by_role[contract.role] for contract in policy.roles)
    artifacts = tuple(run.artifact for run in selected)
    typed_errors = tuple(
        f"run[{run.role.value}]: artifact does not satisfy the completion policy contract"
        for contract, run in zip(policy.roles, selected, strict=True)
        if not contract.accepts(run.artifact)
    )
    if typed_errors:
        return (), typed_errors
    if len(set(artifacts)) != len(policy.roles):
        return (), ("completion policy roles must bind unique artifacts",)
    return artifacts, ()


def validate_gate_artifact_binding(
    receipt: Any,
    artifact: Digest,
    artifact_bytes: bytes,
    *,
    policy: AuthenticatedGateCompletionPolicy,
) -> tuple[str, ...]:
    """Validate exact bytes and their full receipt/run/cycle/oracle binding without effects."""

    artifacts, extraction_errors = required_gate_artifacts(receipt, policy=policy)
    if extraction_errors:
        return extraction_errors
    if artifact not in artifacts:
        return ("artifact is not required by this receipt",)
    if not isinstance(receipt, dict):
        return ("receipt must be an object",)
    try:
        cycle = _cycle_from_receipt(receipt)
        oracle = OracleBoundary.from_dict(receipt["oracle_boundary"])
        runs = tuple(_run_from_dict(value) for value in receipt["runs"])
    except (KeyError, TypeError, ValueError) as error:
        return (f"receipt context cannot be reconstructed: {error}",)
    runs_by_role = {item.role: item for item in runs}
    contracts_by_artifact = {runs_by_role[item.role].artifact: item for item in policy.roles}
    contract = contracts_by_artifact.get(artifact)
    if contract is None:
        return ("artifact has no policy role contract",)
    run = runs_by_role[contract.role]
    document, local_errors = parse_and_validate_artifact_bytes(
        artifact, artifact_bytes, contract=contract
    )
    if local_errors or document is None:
        return local_errors
    return tuple(
        validate_gate_evidence(
            document,
            expected_artifact=artifact,
            expected_run=run,
            expected_cycle=cycle,
            expected_oracle=oracle,
        )
    )


def _assess_resolved_run(
    run: RunEvidence,
    evidence: object,
    context: _RunAssessmentContext,
) -> _RunAssessment:
    """Validate one resolved value without lookup, mutation, or external effects."""

    label = f"run[{run.role.value}]"
    artifact = run.artifact
    if evidence is None:
        return _RunAssessment((f"{label}: gate evidence artifact was not resolved",))
    if not isinstance(evidence, ResolvedGateEvidence):
        return _RunAssessment((f"{label}: resolved gate evidence has an invalid type",))
    if evidence.artifact != artifact:
        return _RunAssessment((f"{label}: resolver returned a different artifact identity",))
    contract = next(item for item in context.policy.roles if item.role is run.role)
    document, local_errors = parse_and_validate_artifact_bytes(
        artifact, evidence.artifact_bytes, contract=contract
    )
    if local_errors or document is None:
        return _RunAssessment(tuple(f"{label}: {error}" for error in local_errors))
    binding_errors = validate_gate_evidence(
        document,
        expected_artifact=artifact,
        expected_run=run,
        expected_cycle=context.cycle,
        expected_oracle=context.oracle,
    )
    if binding_errors:
        return _RunAssessment(tuple(f"{label}: {error}" for error in binding_errors))

    verdict = evidence.authority
    authority_errors: list[str] = []
    if (
        context.policy.expected_authority_id is not None
        and verdict.authority_id != context.policy.expected_authority_id
    ):
        authority_errors.append("authority identity does not match the expected verifier identity")
    if context.policy.require_authentication and not verdict.authenticated:
        authority_errors.append("external authority did not authenticate the producer")
    if context.policy.require_independence and not verdict.independent:
        authority_errors.append("evidence authority is not independent")
    if authority_errors:
        return _RunAssessment(tuple(f"{label}: {error}" for error in authority_errors))
    return _RunAssessment((), artifact)


def assess_authenticated_gate_completion(
    receipt: Any,
    resolved: Mapping[Digest, ResolvedGateEvidence],
    *,
    policy: AuthenticatedGateCompletionPolicy,
) -> CompletionAssessment:
    """Assess already resolved artifacts against receipt context and explicit policy."""

    if not isinstance(policy, AuthenticatedGateCompletionPolicy):
        raise TypeError("policy must be an AuthenticatedGateCompletionPolicy")
    artifacts, extraction_errors = required_gate_artifacts(receipt, policy=policy)
    if extraction_errors or not isinstance(receipt, dict):
        return CompletionAssessment(policy, False, extraction_errors, ())
    try:
        cycle = _cycle_from_receipt(receipt)
        oracle = OracleBoundary.from_dict(receipt["oracle_boundary"])
        runs = tuple(_run_from_dict(value) for value in receipt["runs"])
    except (KeyError, TypeError, ValueError) as error:
        return CompletionAssessment(
            policy,
            False,
            (f"receipt context cannot be reconstructed: {error}",),
            (),
        )
    runs_by_role = {run.role: run for run in runs}
    selected_runs = tuple(runs_by_role[contract.role] for contract in policy.roles)
    context = _RunAssessmentContext(cycle, oracle, policy)
    try:
        resolved_snapshot = dict(resolved)
    except Exception as error:
        return CompletionAssessment(
            policy,
            False,
            (f"resolved evidence mapping cannot be snapshotted: {type(error).__name__}: {error}",),
            (),
        )

    errors: list[str] = []
    validated: list[Digest] = []
    for run, artifact in zip(selected_runs, artifacts, strict=True):
        run_assessment = _assess_resolved_run(
            run,
            resolved_snapshot.get(artifact),
            context,
        )
        errors.extend(run_assessment.errors)
        if run_assessment.validated_artifact is not None:
            validated.append(run_assessment.validated_artifact)

    satisfied = not errors and len(validated) == len(policy.roles)
    return CompletionAssessment(policy, satisfied, tuple(errors), tuple(validated))


__all__ = (
    "AuthenticatedGateCompletionPolicy",
    "AuthorityVerdict",
    "CompletionRoleContract",
    "CompletionAssessment",
    "ResolvedGateEvidence",
    "assess_authenticated_gate_completion",
    "parse_and_validate_artifact_bytes",
    "ooptdd_mutation_v2_completion_policy",
    "required_gate_artifacts",
    "validate_gate_artifact_binding",
)
