"""Effectful ports and shell for authenticated gate completion."""

from __future__ import annotations

from typing import Any, Protocol

from .completion import (
    AuthenticatedGateCompletionPolicy,
    AuthorityVerdict,
    CompletionAssessment,
    ResolvedGateEvidence,
    assess_authenticated_gate_completion,
    required_gate_artifacts,
    validate_gate_artifact_binding,
)
from .identity import Digest


class GateEvidenceResolver(Protocol):
    def get(self, artifact: Digest) -> bytes | None: ...


class GateEvidenceAuthority(Protocol):
    """Caller-selected verifier; ``verify`` judges bytes and reports its claimed identity."""

    def verify(self, artifact: Digest, artifact_bytes: bytes) -> AuthorityVerdict: ...


def resolve_and_assess_authenticated_gate_completion(
    receipt: Any,
    *,
    resolver: GateEvidenceResolver,
    authority: GateEvidenceAuthority,
    policy: AuthenticatedGateCompletionPolicy,
) -> CompletionAssessment:
    """Resolve, locally validate, externally authenticate, then assess evidence."""

    if not isinstance(policy, AuthenticatedGateCompletionPolicy):
        raise TypeError("policy must be an AuthenticatedGateCompletionPolicy")
    artifacts, extraction_errors = required_gate_artifacts(receipt)
    if extraction_errors:
        return CompletionAssessment(policy, False, extraction_errors, ())

    resolved: dict[Digest, ResolvedGateEvidence] = {}
    shell_errors: list[str] = []
    for artifact in artifacts:
        try:
            payload = resolver.get(artifact)
        except Exception as error:
            shell_errors.append(f"resolver failed: {type(error).__name__}: {error}")
            continue
        if payload is None:
            continue
        binding_errors = validate_gate_artifact_binding(receipt, artifact, payload)
        if binding_errors:
            shell_errors.extend(binding_errors)
            continue
        try:
            verdict = authority.verify(artifact, payload)
        except Exception as error:
            shell_errors.append(f"authority failed: {type(error).__name__}: {error}")
            continue
        if not isinstance(verdict, AuthorityVerdict):
            shell_errors.append("authority returned an invalid verdict type")
            continue
        resolved[artifact] = ResolvedGateEvidence(artifact, payload, verdict)

    assessment = assess_authenticated_gate_completion(
        receipt, resolved, policy=policy
    )
    if not shell_errors:
        return assessment
    return CompletionAssessment(
        policy,
        False,
        tuple(shell_errors) + assessment.errors,
        assessment.validated_artifacts,
    )


__all__ = (
    "GateEvidenceAuthority",
    "GateEvidenceResolver",
    "resolve_and_assess_authenticated_gate_completion",
)
