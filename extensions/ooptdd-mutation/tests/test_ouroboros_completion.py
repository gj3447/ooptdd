from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError, replace

import pytest
from ooptdd.domain.ports import BackendCaps, QueryResult
from ooptdd.engine.verify import verify_gate

from ooptdd_mutation.ouroboros import (
    GATE_EVIDENCE_VERSION,
    AuthorityVerdict,
    CompletionAssessment,
    CycleIdentity,
    CycleSnapshot,
    Digest,
    EventKind,
    MaterialLock,
    OracleBoundary,
    ProtocolBudget,
    ProtocolEvent,
    ResolvedGateEvidence,
    RunOutcome,
    RunRole,
    adapt_gate_verification,
    assess_authenticated_gate_completion,
    canonical_json_bytes,
    digest_json,
    digest_raw,
    ooptdd_mutation_v2_completion_policy,
    receipt_content_digest,
    receipt_from_snapshot,
    resolve_and_assess_authenticated_gate_completion,
    step,
    validate_receipt,
)

_POLICY = ooptdd_mutation_v2_completion_policy("authority://test")


def test_completion_policy_is_frozen_and_has_no_hidden_constructor_defaults():
    with pytest.raises(TypeError):
        type(_POLICY)(name="incomplete")  # type: ignore[call-arg]
    with pytest.raises(FrozenInstanceError):
        _POLICY.name = "changed"  # type: ignore[misc]


class _Clock:
    def __init__(self) -> None:
        self.value = 1_000_000

    def now_us(self) -> int:
        self.value += 1
        return self.value


class _Backend:
    default_lookback_s = 3600
    default_future_buffer_s = 0
    caps = BackendCaps(queryable=True, supports_where=True, independent=True)

    def __init__(self, events: list[dict]) -> None:
        self.events = events

    def identity(self) -> str:
        return "store://emit"

    def ship(self, events: list[dict]) -> None:
        raise AssertionError("read-only test backend")

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        return QueryResult(reachable=True, complete=True, events=list(self.events))


def _digest(label: str, scope: str = "test"):
    return digest_raw(label.encode(), scope=scope, schema_version="test/v1")


def _advance(snapshot: CycleSnapshot, ordinal: int, kind: EventKind, payload: dict):
    result = step(
        snapshot,
        ProtocolEvent.create("cycle", f"event-{ordinal}", kind, payload),
    )
    assert result.accepted, result.rejection_code
    return result.snapshot


def _verification(present: bool) -> dict:
    event = {"event": "ready", "_timestamp": 1}
    return verify_gate(
        _Backend([event] if present else []),
        "cycle",
        {"expect": [{"event": "ready"}]},
        retries=1,
        settle_early=False,
        clock=_Clock(),
        sleeper=lambda _: None,
    )


def _receipt_and_artifacts(*, duplicate_artifact: bool = False, context_mismatch: bool = False):
    materials = MaterialLock(
        spec=_digest("spec", "spec"),
        verifier=_digest("verifier", "verifier"),
        source=_digest("source", "source"),
        environment=_digest("environment", "environment"),
        source_commit="0123456789abcdef0123456789abcdef01234567",
    )
    oracle = OracleBoundary("store://emit", "store://emit", False, False)
    mutated = _digest("mutated", "source")
    snapshot = CycleSnapshot.start("cycle", ProtocolBudget(16, 1))
    snapshot = _advance(snapshot, 1, EventKind.SIZE, {"policy_version": "v1"})
    snapshot = _advance(
        snapshot,
        2,
        EventKind.LOCK,
        {"materials": materials.to_dict(), "oracle": oracle.to_dict()},
    )
    red = _verification(False)
    green = _verification(True)

    def bundle(role: RunRole, outcome: RunOutcome, executed_source=None):
        return adapt_gate_verification(
            verification=green if outcome is RunOutcome.GREEN else red,
            cycle=CycleIdentity("cycle"),
            role=role,
            run_id=f"run-{role.value}",
            artifact_namespace=f"artifacts/{role.value}",
            subject_outcome=outcome,
            material_lock=materials,
            executed_source=executed_source or materials.source,
            oracle=oracle,
        )

    initial = bundle(RunRole.INITIAL_RED, RunOutcome.RED)
    if context_mismatch:
        document = json.loads(initial.artifact_bytes)
        document["binding"]["run_id"] = "run-from-another-context"
        artifact = digest_json(
            document,
            scope=initial.artifact.scope,
            schema_version=initial.artifact.schema_version,
        )
        initial = replace(
            initial,
            artifact_bytes=canonical_json_bytes(document),
            artifact=artifact,
            run=replace(initial.run, artifact=artifact),
        )
    positive = bundle(RunRole.POSITIVE, RunOutcome.GREEN)
    if duplicate_artifact:
        positive = replace(
            positive,
            artifact=initial.artifact,
            run=replace(positive.run, artifact=initial.artifact),
        )
    negative = bundle(RunRole.NEGATIVE, RunOutcome.RED, mutated)
    regreen = bundle(RunRole.REGREEN, RunOutcome.GREEN)
    snapshot = _advance(snapshot, 3, EventKind.INITIAL_RED, initial.event_payload)
    snapshot = _advance(snapshot, 4, EventKind.GREEN, positive.event_payload)
    snapshot = _advance(
        snapshot,
        5,
        EventKind.QUARANTINE,
        {
            "artifact_namespace": "artifacts/quarantine",
            "quarantine": _digest("quarantine", "quarantine").to_dict(),
        },
    )
    snapshot = _advance(
        snapshot,
        6,
        EventKind.MUTATION_APPLIED,
        {
            "mutation_delta": _digest("delta", "mutation").to_dict(),
            "source_before": materials.source.to_dict(),
            "source_after": mutated.to_dict(),
        },
    )
    snapshot = _advance(snapshot, 7, EventKind.NEGATIVE_RED, negative.event_payload)
    snapshot = _advance(
        snapshot, 8, EventKind.RESTORE, {"restored_source": materials.source.to_dict()}
    )
    snapshot = _advance(snapshot, 9, EventKind.REGREEN, regreen.event_payload)
    snapshot = _advance(snapshot, 10, EventKind.ENUMERATE_FINDINGS, {"finding_ids": []})
    snapshot = _advance(snapshot, 11, EventKind.SEAL, {})
    bundles = (initial, positive, negative, regreen)
    return (
        receipt_from_snapshot(snapshot, receipt_id="authoritative-receipt"),
        {item.artifact: item.artifact_bytes for item in bundles},
    )


class _Resolver:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.calls = []

    def get(self, artifact):
        self.calls.append(artifact)
        return self.artifacts.get(artifact)


class _Authority:
    def __init__(
        self,
        *,
        authenticated: bool = True,
        independent: bool = True,
        authority_id: str = "authority://test",
    ):
        self.authenticated = authenticated
        self.independent = independent
        self.authority_id = authority_id
        self.seen = []

    def verify(self, artifact, artifact_bytes):
        self.seen.append((artifact, artifact_bytes))
        return AuthorityVerdict(
            authenticated=self.authenticated,
            authority_id=self.authority_id,
            independent=self.independent,
        )


def test_authenticated_gate_completion_resolves_revalidates_and_authenticates_all_runs():
    receipt, artifacts = _receipt_and_artifacts()
    resolver = _Resolver(artifacts)
    authority = _Authority()

    result = resolve_and_assess_authenticated_gate_completion(
        receipt, resolver=resolver, authority=authority, policy=_POLICY
    )

    assert result.satisfied
    assert result.errors == ()
    assert len(result.validated_artifacts) == 4
    assert len(set(result.validated_artifacts)) == 4
    assert len(resolver.calls) == 4
    assert authority.seen == [(digest, artifacts[digest]) for digest in resolver.calls]


@pytest.mark.parametrize("role_count", [0, 1, 3, 4])
def test_completion_policy_explicitly_selects_zero_one_three_or_four_roles(role_count):
    receipt, artifacts = _receipt_and_artifacts()
    policy = replace(_POLICY, roles=_POLICY.roles[:role_count])
    resolver = _Resolver(artifacts)
    authority = _Authority()

    result = resolve_and_assess_authenticated_gate_completion(
        receipt, resolver=resolver, authority=authority, policy=policy
    )

    assert result.satisfied
    assert len(result.validated_artifacts) == role_count
    assert len(resolver.calls) == role_count
    assert len(authority.seen) == role_count


def test_authentication_and_independence_are_explicit_policy_requirements():
    receipt, artifacts = _receipt_and_artifacts()
    policy = replace(
        _POLICY,
        expected_authority_id=None,
        require_authentication=False,
        require_independence=False,
    )
    result = resolve_and_assess_authenticated_gate_completion(
        receipt,
        resolver=_Resolver(artifacts),
        authority=_Authority(authenticated=False, independent=False),
        policy=policy,
    )
    assert result.satisfied


def test_role_artifact_contract_is_enforced_from_policy():
    receipt, artifacts = _receipt_and_artifacts()
    wrong = replace(_POLICY.roles[0], artifact_scope="another-artifact-type")
    policy = replace(_POLICY, roles=(wrong,) + _POLICY.roles[1:])
    resolver = _Resolver(artifacts)

    result = resolve_and_assess_authenticated_gate_completion(
        receipt, resolver=resolver, authority=_Authority(), policy=policy
    )

    assert not result.satisfied
    assert "does not satisfy the completion policy contract" in result.errors[0]
    assert resolver.calls == []


def test_unresolved_gate_artifact_fails_while_receipt_validation_stays_unchanged():
    receipt, artifacts = _receipt_and_artifacts()
    assert validate_receipt(receipt) == []
    artifacts.pop(next(iter(artifacts)))

    result = resolve_and_assess_authenticated_gate_completion(
        receipt, resolver=_Resolver(artifacts), authority=_Authority(), policy=_POLICY
    )

    assert not result.satisfied
    assert any("was not resolved" in error for error in result.errors)
    assert validate_receipt(receipt) == []


def test_artifact_self_claim_cannot_replace_external_authority():
    receipt, artifacts = _receipt_and_artifacts()

    result = resolve_and_assess_authenticated_gate_completion(
        receipt,
        resolver=_Resolver(artifacts),
        authority=_Authority(authenticated=False),
        policy=_POLICY,
    )

    assert not result.satisfied
    assert sum("did not authenticate" in error for error in result.errors) == 4


def test_noncanonical_or_digest_mismatched_exact_bytes_fail_closed():
    receipt, artifacts = _receipt_and_artifacts()
    target = next(iter(artifacts))
    document = json.loads(artifacts[target])
    altered = copy.deepcopy(artifacts)
    altered[target] = b" " + canonical_json_bytes(document)

    result = resolve_and_assess_authenticated_gate_completion(
        receipt, resolver=_Resolver(altered), authority=_Authority(), policy=_POLICY
    )

    assert not result.satisfied
    assert any("not the canonical JSON encoding" in error for error in result.errors)


def test_independent_authority_is_required_even_when_authentication_succeeds():
    receipt, artifacts = _receipt_and_artifacts()
    result = resolve_and_assess_authenticated_gate_completion(
        receipt,
        resolver=_Resolver(artifacts),
        authority=_Authority(independent=False),
        policy=_POLICY,
    )

    assert not result.satisfied
    assert sum("authority is not independent" in error for error in result.errors) == 4


def test_verifier_claimed_authority_id_must_match_policy():
    receipt, artifacts = _receipt_and_artifacts()

    result = resolve_and_assess_authenticated_gate_completion(
        receipt,
        resolver=_Resolver(artifacts),
        authority=_Authority(authority_id="authority://other"),
        policy=_POLICY,
    )

    assert not result.satisfied
    assert sum("identity does not match" in error for error in result.errors) == 4


def test_canonical_digest_tamper_is_rejected_before_authority_is_called():
    receipt, artifacts = _receipt_and_artifacts()
    target = next(iter(artifacts))
    document = json.loads(artifacts[target])
    document["binding"]["run_id"] = "run-tampered"
    altered = copy.deepcopy(artifacts)
    altered[target] = canonical_json_bytes(document)
    authority = _Authority()

    result = resolve_and_assess_authenticated_gate_completion(
        receipt,
        resolver=_Resolver(altered),
        authority=authority,
        policy=_POLICY,
    )

    assert not result.satisfied
    assert any("do not match the receipt artifact digest" in error for error in result.errors)
    assert all(artifact != target for artifact, _ in authority.seen)


def test_context_binding_tamper_is_rejected_before_authority_is_called():
    receipt, artifacts = _receipt_and_artifacts(context_mismatch=True)
    target = next(iter(artifacts))
    authority = _Authority()

    assert validate_receipt(receipt) == []
    result = resolve_and_assess_authenticated_gate_completion(
        receipt,
        resolver=_Resolver(artifacts),
        authority=authority,
        policy=_POLICY,
    )

    assert not result.satisfied
    assert any("run_id" in error for error in result.errors)
    assert all(artifact != target for artifact, _ in authority.seen)


def test_tampered_receipt_context_fails_before_resolution():
    receipt, artifacts = _receipt_and_artifacts()
    receipt["oracle_boundary"]["observation_store"] = "store://other"
    receipt["integrity"]["value"] = receipt_content_digest(
        receipt, schema_version=receipt["schema_version"]
    ).value
    resolver = _Resolver(artifacts)

    result = resolve_and_assess_authenticated_gate_completion(
        receipt, resolver=resolver, authority=_Authority(), policy=_POLICY
    )

    assert not result.satisfied
    assert any("receipt:" in error for error in result.errors)
    assert resolver.calls == []


def test_duplicate_artifact_across_roles_is_rejected_before_resolution():
    receipt, artifacts = _receipt_and_artifacts(duplicate_artifact=True)
    assert validate_receipt(receipt) == []
    resolver = _Resolver(artifacts)

    result = resolve_and_assess_authenticated_gate_completion(
        receipt, resolver=resolver, authority=_Authority(), policy=_POLICY
    )

    assert not result.satisfied
    assert result.errors == ("completion policy roles must bind unique artifacts",)
    assert resolver.calls == []


class _FailingResolver:
    def get(self, artifact):
        raise RuntimeError(f"offline:{artifact.value[:8]}")


class _InvalidResolver:
    def get(self, artifact):
        return "not-bytes"


class _FailingAuthority:
    def verify(self, artifact, artifact_bytes):
        raise RuntimeError(f"denied:{artifact.value[:8]}")


class _InvalidAuthority:
    def verify(self, artifact, artifact_bytes):
        return object()


@pytest.mark.parametrize(
    ("resolver", "authority", "expected"),
    [
        (_FailingResolver(), _Authority(), "resolver failed: RuntimeError"),
        (_InvalidResolver(), _Authority(), "artifact payload must be exact bytes"),
        (_Resolver({}), _Authority(), "was not resolved"),
    ],
)
def test_resolver_failures_and_invalid_values_fail_closed(resolver, authority, expected):
    receipt, _ = _receipt_and_artifacts()

    result = resolve_and_assess_authenticated_gate_completion(
        receipt, resolver=resolver, authority=authority, policy=_POLICY
    )

    assert not result.satisfied
    assert any(expected in error for error in result.errors)


@pytest.mark.parametrize(
    ("authority", "expected"),
    [
        (_FailingAuthority(), "authority failed: RuntimeError"),
        (_InvalidAuthority(), "authority returned an invalid verdict type"),
    ],
)
def test_authority_exceptions_and_invalid_values_fail_closed(authority, expected):
    receipt, artifacts = _receipt_and_artifacts()

    result = resolve_and_assess_authenticated_gate_completion(
        receipt, resolver=_Resolver(artifacts), authority=authority, policy=_POLICY
    )

    assert not result.satisfied
    assert any(expected in error for error in result.errors)


def test_failing_shell_is_deterministic_for_the_same_inputs():
    receipt, _ = _receipt_and_artifacts()

    first = resolve_and_assess_authenticated_gate_completion(
        receipt, resolver=_FailingResolver(), authority=_Authority(), policy=_POLICY
    )
    second = resolve_and_assess_authenticated_gate_completion(
        receipt, resolver=_FailingResolver(), authority=_Authority(), policy=_POLICY
    )

    assert first == second
    assert not first.satisfied


def test_public_completion_values_reject_malformed_or_contradictory_states():
    artifact = _digest("artifact")
    verdict = AuthorityVerdict(True, "authority://test", True)

    with pytest.raises(TypeError, match="artifact must be a Digest"):
        ResolvedGateEvidence("bad", b"{}", verdict)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact bytes"):
        ResolvedGateEvidence(artifact, bytearray(b"{}"), verdict)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="AuthorityVerdict"):
        ResolvedGateEvidence(artifact, b"{}", object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="satisfied assessment"):
        CompletionAssessment(_POLICY, True, ("contradiction",), ())
    with pytest.raises(ValueError, match="one artifact per policy role"):
        CompletionAssessment(_POLICY, True, (), ())
    wrong_type = tuple(_digest(f"wrong-type-{index}") for index in range(4))
    with pytest.raises(ValueError, match="policy-conforming artifacts"):
        CompletionAssessment(_POLICY, True, (), wrong_type)
    wrong_canonicalization = tuple(
        Digest(
            "sha256",
            "ouroboros-gate-evidence",
            "raw-bytes",
            GATE_EVIDENCE_VERSION,
            f"{index + 1:064x}",
        )
        for index in range(4)
    )
    with pytest.raises(ValueError, match="policy-conforming artifacts"):
        CompletionAssessment(_POLICY, True, (), wrong_canonicalization)


def test_local_assessment_treats_malformed_resolved_mapping_as_failure_not_crash():
    receipt, artifacts = _receipt_and_artifacts()

    class _GetForbiddenDict(dict):
        def get(self, key, default=None):
            raise AssertionError("assessment must use its immutable input snapshot")

    malformed = _GetForbiddenDict({artifact: object() for artifact in artifacts})

    result = assess_authenticated_gate_completion(
        receipt,
        malformed,
        policy=_POLICY,  # type: ignore[arg-type]
    )

    assert not result.satisfied
    assert sum("invalid type" in error for error in result.errors) == 4
