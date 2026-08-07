from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ooptdd.domain.ports import BackendCaps, ProbeResult, QueryResult
from ooptdd.engine.gate import check, evidence_tier, unregister
from ooptdd.engine.verify import verify_gate
from ooptdd.ouroboros import (
    OUROBOROS_GATE_EVIDENCE_SCHEMA,
    CycleIdentity,
    CycleSnapshot,
    EventKind,
    EvidenceTier,
    MaterialLock,
    MonitorVerdict,
    ObservationVerdict,
    OracleBoundary,
    Phase,
    ProtocolBudget,
    ProtocolEvent,
    RunOutcome,
    RunRole,
    adapt_gate_verification,
    digest_raw,
    receipt_from_snapshot,
    step,
    validate_gate_evidence,
    validate_receipt,
)

ROOT = Path(__file__).resolve().parents[1]


class _Clock:
    def __init__(self) -> None:
        self.value = 1_000_000_000

    def now_us(self) -> int:
        self.value += 1
        return self.value


class _Backend:
    default_lookback_s = 3600
    default_future_buffer_s = 0

    def __init__(
        self,
        events: list[dict],
        *,
        reachable: bool = True,
        complete: bool = True,
        identity: str = "store://emit",
        samples: bool = False,
    ) -> None:
        self.events = events
        self.reachable = reachable
        self.complete = complete
        self._identity = identity
        self.calls = 0
        self.caps = BackendCaps(
            queryable=True,
            supports_where=True,
            independent=True,
            samples=samples,
        )

    def identity(self) -> str:
        return self._identity

    def ship(self, events: list[dict]) -> None:  # pragma: no cover - read-only test adapter
        raise AssertionError("test backend is read-only")

    def query(self, cid: str, *, since_us: int, until_us: int) -> QueryResult:
        self.calls += 1
        return QueryResult(
            reachable=self.reachable,
            complete=self.complete,
            events=list(self.events),
        )


class _Probe:
    def __init__(
        self,
        value: object,
        *,
        read_identity: str,
        separate_source: bool = True,
        reachable: bool = True,
    ) -> None:
        self.value = value
        self.read_identity = read_identity
        self.separate_source = separate_source
        self.reachable = reachable

    def probe(self, kind: str, selector: object, cid: str) -> ProbeResult:
        return ProbeResult(
            reachable=self.reachable,
            value=self.value,
            separate_source=self.separate_source,
            derived_identity=self.read_identity,
        )


def _digest(label: str, scope: str = "test-artifact"):
    return digest_raw(label.encode(), scope=scope, schema_version="test/v1")


def _materials() -> MaterialLock:
    return MaterialLock(
        spec=_digest("spec", "spec"),
        verifier=_digest("verifier", "verifier"),
        source=_digest("source", "source"),
        environment=_digest("environment", "environment"),
        source_commit="0123456789abcdef0123456789abcdef01234567",
    )


def _self_oracle() -> OracleBoundary:
    return OracleBoundary(
        emit_identity="store://emit",
        read_identity="store://emit",
        separate_source=False,
        corroborated=False,
    )


def _external_oracle(read_identity: str = "oracle://read") -> OracleBoundary:
    return OracleBoundary(
        emit_identity="store://emit",
        read_identity=read_identity,
        separate_source=True,
        corroborated=True,
    )


def _verify(
    spec: dict,
    events: list[dict],
    *,
    backend: _Backend | None = None,
    probe: _Probe | None = None,
    retries: int = 1,
    settle_early: bool = False,
) -> dict:
    return verify_gate(
        backend or _Backend(events),
        "cycle-0",
        spec,
        retries=retries,
        settle_early=settle_early,
        clock=_Clock(),
        sleeper=lambda _seconds: None,
        probe=probe,
    )


def _adapt(
    verification: dict,
    *,
    role: RunRole,
    subject_outcome: RunOutcome,
    materials: MaterialLock | None = None,
    executed_source=None,
    oracle: OracleBoundary | None = None,
):
    materials = materials or _materials()
    return adapt_gate_verification(
        verification=verification,
        cycle=CycleIdentity("cycle-0"),
        role=role,
        run_id=f"run-{role.value}",
        artifact_namespace=f"artifacts/{role.value}",
        subject_outcome=subject_outcome,
        material_lock=materials,
        executed_source=executed_source or materials.source,
        oracle=oracle or _self_oracle(),
    )


def _positive_bundle():
    verification = _verify(
        {"expect": [{"event": "ready", "where": {"status": "ok"}}]},
        [{"event": "ready", "status": "ok", "_timestamp": 1}],
    )
    materials = _materials()
    bundle = _adapt(
        verification,
        role=RunRole.POSITIVE,
        subject_outcome=RunOutcome.GREEN,
        materials=materials,
    )
    return bundle, verification, materials


def _errors(document: dict) -> str:
    return "\n".join(validate_gate_evidence(document))


def _advance(snapshot: CycleSnapshot, number: int, kind: EventKind, payload: dict) -> CycleSnapshot:
    event = ProtocolEvent.create("cycle-0", f"event-{number:02d}", kind, payload)
    result = step(snapshot, event)
    assert result.accepted, result.rejection_code
    return result.snapshot


def test_verify_gate_marks_early_prefix_and_can_force_bounded_final() -> None:
    spec = {"expect": [{"event": "ready", "op": ">=", "count": 1}]}
    events = [{"event": "ready", "_timestamp": 1}]

    early_backend = _Backend(events)
    early = _verify(
        spec,
        events,
        backend=early_backend,
        retries=3,
        settle_early=True,
    )
    assert early["settlement"] == "irrevocable_prefix"
    assert early["attempts"] == 1 == early_backend.calls

    final_backend = _Backend(events)
    final = _verify(spec, events, backend=final_backend, retries=3)
    assert final["settlement"] == "bounded_final"
    assert final["attempts"] == 3 == final_backend.calls

    with pytest.raises(TypeError, match="settle_early must be a boolean"):
        _verify(spec, events, settle_early="false")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="bounded_final"):
        _adapt(
            early,
            role=RunRole.POSITIVE,
            subject_outcome=RunOutcome.GREEN,
        )


def test_positive_adapter_is_deterministic_and_binds_all_external_context() -> None:
    bundle, verification, materials = _positive_bundle()
    repeated = _adapt(
        verification,
        role=RunRole.POSITIVE,
        subject_outcome=RunOutcome.GREEN,
        materials=materials,
    )

    assert bundle.artifact_bytes == repeated.artifact_bytes
    assert bundle.artifact == repeated.artifact
    assert bundle.run.monitor is MonitorVerdict.PEND
    assert bundle.run.observation is ObservationVerdict.PRESENT
    assert bundle.run.outcome is RunOutcome.GREEN
    assert bundle.run.evidence_tier is EvidenceTier.ARRIVED
    assert "role" not in bundle.event_payload
    assert validate_gate_evidence(
        bundle.document,
        expected_artifact=bundle.artifact,
        expected_run=bundle.run,
        expected_cycle=CycleIdentity("cycle-0"),
        expected_oracle=_self_oracle(),
        expected_verification=verification,
    ) == []


def test_adapter_payload_is_accepted_by_the_existing_v2_reducer() -> None:
    materials = _materials()
    snapshot = CycleSnapshot.start(
        "cycle-0", ProtocolBudget(max_steps=6, max_generations=1)
    )
    size = ProtocolEvent.create("cycle-0", "event-size", EventKind.SIZE, {"policy_version": "v1"})
    snapshot = step(snapshot, size).snapshot
    lock = ProtocolEvent.create(
        "cycle-0",
        "event-lock",
        EventKind.LOCK,
        {"materials": materials.to_dict(), "oracle": _self_oracle().to_dict()},
    )
    snapshot = step(snapshot, lock).snapshot
    red_verification = _verify(
        {"expect": [{"event": "must-not-be-ready"}]},
        [],
    )
    bundle = _adapt(
        red_verification,
        role=RunRole.INITIAL_RED,
        subject_outcome=RunOutcome.RED,
        materials=materials,
    )
    event = ProtocolEvent.create(
        "cycle-0", "event-red", EventKind.INITIAL_RED, bundle.event_payload
    )
    result = step(snapshot, event)

    assert result.accepted
    assert result.snapshot.phase is Phase.INITIAL_RED_CONFIRMED
    assert result.snapshot.runs == (bundle.run,)


def test_adapter_drives_all_four_roles_into_a_valid_complete_receipt() -> None:
    materials = _materials()
    oracle = _self_oracle()
    mutated_source = _digest("mutated-source", "source")
    snapshot = CycleSnapshot.start(
        "cycle-0", ProtocolBudget(max_steps=16, max_generations=1)
    )
    snapshot = _advance(snapshot, 1, EventKind.SIZE, {"policy_version": "v1"})
    snapshot = _advance(
        snapshot,
        2,
        EventKind.LOCK,
        {"materials": materials.to_dict(), "oracle": oracle.to_dict()},
    )

    red_verification = _verify({"expect": [{"event": "missing"}]}, [])
    initial = _adapt(
        red_verification,
        role=RunRole.INITIAL_RED,
        subject_outcome=RunOutcome.RED,
        materials=materials,
        oracle=oracle,
    )
    snapshot = _advance(snapshot, 3, EventKind.INITIAL_RED, initial.event_payload)

    green_verification = _verify(
        {"expect": [{"event": "ready"}]},
        [{"event": "ready", "_timestamp": 1}],
    )
    positive = _adapt(
        green_verification,
        role=RunRole.POSITIVE,
        subject_outcome=RunOutcome.GREEN,
        materials=materials,
        oracle=oracle,
    )
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
            "source_after": mutated_source.to_dict(),
        },
    )
    negative = _adapt(
        red_verification,
        role=RunRole.NEGATIVE,
        subject_outcome=RunOutcome.RED,
        materials=materials,
        executed_source=mutated_source,
        oracle=oracle,
    )
    snapshot = _advance(snapshot, 7, EventKind.NEGATIVE_RED, negative.event_payload)
    snapshot = _advance(
        snapshot,
        8,
        EventKind.RESTORE,
        {"restored_source": materials.source.to_dict()},
    )
    regreen = _adapt(
        green_verification,
        role=RunRole.REGREEN,
        subject_outcome=RunOutcome.GREEN,
        materials=materials,
        oracle=oracle,
    )
    snapshot = _advance(snapshot, 9, EventKind.REGREEN, regreen.event_payload)
    snapshot = _advance(
        snapshot, 10, EventKind.ENUMERATE_FINDINGS, {"finding_ids": []}
    )
    snapshot = _advance(snapshot, 11, EventKind.SEAL, {})

    receipt = receipt_from_snapshot(snapshot, receipt_id="receipt-adapter-cycle")
    assert validate_receipt(receipt) == []
    assert receipt["status"] == "complete"
    assert [run["artifact"] for run in receipt["runs"]] == [
        bundle.artifact.to_dict()
        for bundle in (initial, positive, negative, regreen)
    ]


def test_projection_preserves_optional_pending_and_custom_check_semantics() -> None:
    @check("adapter_custom_check")
    def _custom(events, rule, ctx):
        return {
            "passed": True,
            "label": "custom-proof",
            "strength": "custom-proof",
            "charged": True,
        }

    try:
        verification = _verify(
            {
                "expect": [
                    {"event": "ready"},
                    {"event": "optional-miss", "optional": True},
                    {"event": "pending-miss", "pending": True},
                    {"adapter_custom_check": True},
                ]
            },
            [{"event": "ready", "_timestamp": 1}],
        )
        bundle = _adapt(
            verification,
            role=RunRole.POSITIVE,
            subject_outcome=RunOutcome.GREEN,
        )
    finally:
        unregister("adapter_custom_check")

    checks = bundle.document["checks"]
    assert bundle.document["aggregation"]["ok"] is True
    assert checks[1]["optional"] is True and checks[1]["passed"] is False
    assert checks[2]["pending"] is True and checks[2]["passed"] is False
    assert checks[3]["kind"] == "adapter_custom_check"
    assert checks[3]["monitor"] is None
    assert validate_gate_evidence(bundle.document, expected_run=bundle.run) == []


def test_weighted_threshold_keeps_failed_check_without_forging_scalar_monitor() -> None:
    verification = _verify(
        {
            "threshold": 0.6,
            "expect": [{"event": "a"}, {"event": "b"}, {"event": "c"}],
        },
        [
            {"event": "a", "_timestamp": 1},
            {"event": "b", "_timestamp": 2},
        ],
    )
    bundle = _adapt(
        verification,
        role=RunRole.POSITIVE,
        subject_outcome=RunOutcome.GREEN,
    )

    assert bundle.document["aggregation"] == {
        "mode": "weighted_threshold",
        "ok": True,
        "gating_check_ids": [check["check_id"] for check in bundle.document["checks"]],
        "score": repr(2 / 3),
        "threshold": "0.6",
    }
    assert [check["passed"] for check in bundle.document["checks"]] == [True, True, False]
    assert bundle.run.monitor is MonitorVerdict.PEND


def test_external_tier_requires_the_exact_corroborated_read_identity() -> None:
    probe = _Probe(42, read_identity="oracle://read")
    verification = _verify(
        {"expect": [{"external": {"kind": "db", "selector": {}, "want": 42}}]},
        [],
        probe=probe,
    )
    bundle = _adapt(
        verification,
        role=RunRole.POSITIVE,
        subject_outcome=RunOutcome.GREEN,
        oracle=_external_oracle(),
    )
    assert bundle.run.evidence_tier is EvidenceTier.EXTERNAL_VERDICT
    assert bundle.document["oracle"]["read_identities"] == ["oracle://read"]

    with pytest.raises(ValueError, match="bound derived read identity"):
        _adapt(
            verification,
            role=RunRole.POSITIVE,
            subject_outcome=RunOutcome.GREEN,
            oracle=_external_oracle("oracle://wrong"),
        )
    with pytest.raises(ValueError, match="corroborated, distinct"):
        _adapt(
            verification,
            role=RunRole.POSITIVE,
            subject_outcome=RunOutcome.GREEN,
            oracle=OracleBoundary(
                emit_identity="store://emit",
                read_identity="store://emit",
                separate_source=False,
                corroborated=False,
            ),
        )


def test_same_endpoint_external_claim_is_demoted_to_arrived() -> None:
    probe = _Probe(42, read_identity="store://emit")
    verification = _verify(
        {"expect": [{"external": {"kind": "db", "selector": {}, "want": 42}}]},
        [],
        probe=probe,
    )
    bundle = _adapt(
        verification,
        role=RunRole.POSITIVE,
        subject_outcome=RunOutcome.GREEN,
    )
    assert bundle.run.evidence_tier is EvidenceTier.ARRIVED
    assert bundle.document["oracle"]["corroborated"] == 0


def test_infrastructure_uncertainty_is_never_relabelled_as_subject_red_or_green() -> None:
    backend = _Backend([], reachable=False)
    verification = _verify(
        {"expect": [{"event": "ready"}]},
        [],
        backend=backend,
    )
    bundle = _adapt(
        verification,
        role=RunRole.POSITIVE,
        subject_outcome=RunOutcome.GREEN,
    )
    assert bundle.run.outcome is RunOutcome.INCONCLUSIVE
    assert bundle.run.observation is ObservationVerdict.INCONCLUSIVE
    assert bundle.run.evidence_tier is EvidenceTier.LOCAL_PASS
    assert validate_gate_evidence(bundle.document, expected_run=bundle.run) == []


def test_role_source_binding_and_red_subject_are_fail_closed() -> None:
    materials = _materials()
    red = _verify({"expect": [{"event": "missing"}]}, [])
    mutated = _digest("mutated", "source")
    bundle = _adapt(
        red,
        role=RunRole.NEGATIVE,
        subject_outcome=RunOutcome.RED,
        materials=materials,
        executed_source=mutated,
    )
    assert bundle.run.executed_source == mutated

    with pytest.raises(ValueError, match="mutated source"):
        _adapt(
            red,
            role=RunRole.NEGATIVE,
            subject_outcome=RunOutcome.RED,
            materials=materials,
        )
    with pytest.raises(ValueError, match="locked baseline source"):
        _adapt(
            red,
            role=RunRole.POSITIVE,
            subject_outcome=RunOutcome.GREEN,
            materials=materials,
            executed_source=mutated,
        )

    posture_red = _verify(
        {
            "require_corroboration": True,
            "expect": [{"event": "ready"}],
        },
        [{"event": "ready", "_timestamp": 1}],
    )
    with pytest.raises(ValueError, match="failed gating aggregation"):
        _adapt(
            posture_red,
            role=RunRole.INITIAL_RED,
            subject_outcome=RunOutcome.RED,
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda doc: doc["checks"][0].__setitem__("passed", False),
            "check_id does not match",
        ),
        (
            lambda doc: (
                doc["checks"][0].__setitem__("check_id", "f" * 64),
                doc["aggregation"]["gating_check_ids"].__setitem__(0, "f" * 64),
            ),
            "check_id does not match",
        ),
        (
            lambda doc: doc["readback"]["arrival"].__setitem__("waited_ms", "0"),
            "waited_ms must be a non-negative integer",
        ),
        (
            lambda doc: doc["checks"][0].__setitem__("settled_at", "1"),
            "settled_at must be null or non-negative integer",
        ),
        (
            lambda doc: doc["oracle"].__setitem__(
                "evidence_tier", "queryable_causal"
            ),
            "evidence_tier does not match projected checks",
        ),
        (
            lambda doc: doc["binding"].__setitem__(
                "executed_source", _digest("forged", "source").to_dict()
            ),
            "executed_source must equal the locked baseline",
        ),
        (
            lambda doc: doc["binding"]["verification_value"].__setitem__(
                "canonicalization", "raw-bytes"
            ),
            "verification_value has the wrong domain separation",
        ),
        (
            lambda doc: doc["readback"].__setitem__("verdict", []),
            "readback.verdict vocabulary is invalid",
        ),
        (
            lambda doc: doc["checks"][0].__setitem__("monitor", {}),
            "checks[0].monitor vocabulary is invalid",
        ),
        (
            lambda doc: doc["checks"][0].__setitem__("strength", {}),
            "checks[0].strength must be non-empty text",
        ),
        (
            lambda doc: doc["checks"][0].__setitem__("grounding", []),
            "checks[0].grounding vocabulary is invalid",
        ),
    ],
)
def test_validator_rejects_semantic_and_type_tampering(mutate, message: str) -> None:
    bundle, _, _ = _positive_bundle()
    document = copy.deepcopy(bundle.document)
    mutate(document)
    assert message in _errors(document)


def test_validator_recomputes_weighted_score_and_final_ok() -> None:
    verification = _verify(
        {
            "threshold": 0.5,
            "expect": [{"event": "a"}, {"event": "b"}],
        },
        [{"event": "a", "_timestamp": 1}],
    )
    bundle = _adapt(
        verification,
        role=RunRole.POSITIVE,
        subject_outcome=RunOutcome.GREEN,
    )
    forged_score = copy.deepcopy(bundle.document)
    forged_score["aggregation"]["score"] = "0.9"
    forged_score["aggregation"]["threshold"] = "0.8"
    assert "aggregation.score does not match projected checks" in _errors(forged_score)

    forged_ok = copy.deepcopy(bundle.document)
    forged_ok["aggregation"]["ok"] = False
    assert "aggregation.ok does not match checks and oracle posture" in _errors(forged_ok)


def test_verification_commitment_preserves_the_sign_bit_of_float_zero() -> None:
    _, verification, _ = _positive_bundle()
    positive_zero = copy.deepcopy(verification)
    negative_zero = copy.deepcopy(verification)
    positive_zero["gate"]["checks"][0]["weight"] = 0.0
    negative_zero["gate"]["checks"][0]["weight"] = -0.0

    positive_bundle = _adapt(
        positive_zero,
        role=RunRole.POSITIVE,
        subject_outcome=RunOutcome.GREEN,
    )
    negative_bundle = _adapt(
        negative_zero,
        role=RunRole.POSITIVE,
        subject_outcome=RunOutcome.GREEN,
    )

    positive_digest = positive_bundle.document["binding"]["verification_value"]
    negative_digest = negative_bundle.document["binding"]["verification_value"]
    assert positive_digest["value"] != negative_digest["value"]
    assert positive_bundle.artifact != negative_bundle.artifact


def test_validator_checks_receipt_cycle_oracle_and_verification_bindings() -> None:
    bundle, verification, _ = _positive_bundle()
    wrong_run = bundle.run.to_dict()
    wrong_run["run_id"] = "different-run"
    assert "expected_run.run_id does not match" in "\n".join(
        validate_gate_evidence(bundle.document, expected_run=wrong_run)
    )
    assert "expected cycle does not match" in "\n".join(
        validate_gate_evidence(
            bundle.document,
            expected_cycle=CycleIdentity("different-cycle"),
        )
    )
    assert "expected oracle does not match" in "\n".join(
        validate_gate_evidence(
            bundle.document,
            expected_oracle=OracleBoundary(
                emit_identity="other://emit",
                read_identity="other://emit",
                separate_source=False,
                corroborated=False,
            ),
        )
    )
    changed_verification = copy.deepcopy(verification)
    changed_verification["reasons"] = ["forged"]
    assert "expected verification does not match" in "\n".join(
        validate_gate_evidence(
            bundle.document,
            expected_verification=changed_verification,
        )
    )
    assert "expected artifact digest does not match" in "\n".join(
        validate_gate_evidence(
            bundle.document,
            expected_artifact=_digest("wrong-artifact", "ouroboros-gate-evidence"),
        )
    )


def test_sampled_invariant_is_capped_at_arrived() -> None:
    events = [
        {"event": "pay", "amount": 42, "_timestamp": 1},
        {"event": "ship", "amount": 42, "_timestamp": 2},
    ]
    spec = {
        "expect": [
            {
                "invariant": {
                    "left": {"reduce": "sum", "field": "amount", "event": "pay"},
                    "right": {"reduce": "sum", "field": "amount", "event": "ship"},
                    "op": "==",
                    "tol": 0.0,
                }
            }
        ]
    }
    verification = _verify(spec, events, backend=_Backend(events, samples=True))
    bundle = _adapt(
        verification,
        role=RunRole.POSITIVE,
        subject_outcome=RunOutcome.GREEN,
    )
    assert bundle.run.evidence_tier is EvidenceTier.ARRIVED


def test_adapter_evidence_tier_matches_the_engine_ladder_on_representative_results() -> None:
    invariant_spec = {
        "expect": [
            {
                "invariant": {
                    "left": {"reduce": "sum", "field": "amount", "event": "pay"},
                    "right": {"reduce": "sum", "field": "amount", "event": "ship"},
                    "op": "==",
                    "tol": 0.0,
                }
            }
        ]
    }
    invariant_events = [
        {"event": "pay", "amount": 42, "_timestamp": 1},
        {"event": "ship", "amount": 42, "_timestamp": 2},
    ]
    external_probe = _Probe(42, read_identity="oracle://read")
    cases = [
        (
            _verify(
                {"expect": [{"event": "ready"}]},
                [],
                backend=_Backend([], reachable=False),
            ),
            RunRole.POSITIVE,
            RunOutcome.GREEN,
            _self_oracle(),
        ),
        (
            _verify({"expect": [{"event": "missing"}]}, []),
            RunRole.INITIAL_RED,
            RunOutcome.RED,
            _self_oracle(),
        ),
        (
            _verify(
                {"expect": [{"event": "ready"}]},
                [{"event": "ready", "_timestamp": 1}],
            ),
            RunRole.POSITIVE,
            RunOutcome.GREEN,
            _self_oracle(),
        ),
        (
            _verify(invariant_spec, invariant_events),
            RunRole.POSITIVE,
            RunOutcome.GREEN,
            _self_oracle(),
        ),
        (
            _verify(
                invariant_spec,
                invariant_events,
                backend=_Backend(invariant_events, samples=True),
            ),
            RunRole.POSITIVE,
            RunOutcome.GREEN,
            _self_oracle(),
        ),
        (
            _verify(
                {"expect": [{"external": {"kind": "db", "selector": {}, "want": 42}}]},
                [],
                probe=external_probe,
            ),
            RunRole.POSITIVE,
            RunOutcome.GREEN,
            _external_oracle(),
        ),
    ]

    for verification, role, outcome, oracle in cases:
        bundle = _adapt(
            verification,
            role=role,
            subject_outcome=outcome,
            oracle=oracle,
        )
        assert bundle.run.evidence_tier.value == evidence_tier(verification["gate"])


@pytest.mark.parametrize("policy", ["optional", "pending"])
def test_non_gating_causal_check_cannot_authorize_positive_completion(policy: str) -> None:
    invariant = {
        "invariant": {
            "left": {"reduce": "sum", "field": "amount", "event": "pay"},
            "right": {"reduce": "sum", "field": "amount", "event": "ship"},
            "op": "==",
            "tol": 0.0,
        },
        policy: True,
    }
    verification = _verify(
        {"expect": [{"absent": [{"event": "boom"}]}, invariant]},
        [
            {"event": "pay", "amount": 42, "_timestamp": 1},
            {"event": "ship", "amount": 42, "_timestamp": 2},
        ],
    )

    assert verification["gate"]["ok"] is True
    assert evidence_tier(verification["gate"]) == "emitted"
    with pytest.raises(ValueError, match="arrived-or-stronger"):
        _adapt(
            verification,
            role=RunRole.POSITIVE,
            subject_outcome=RunOutcome.GREEN,
        )


def test_adapter_rejects_forged_weight_domains_and_zero_weight_quorum() -> None:
    verification = _verify(
        {
            "threshold": 0.5,
            "expect": [{"event": "a"}, {"event": "b"}],
        },
        [{"event": "a", "_timestamp": 1}],
    )

    negative = copy.deepcopy(verification)
    negative["gate"]["checks"][0]["weight"] = -1.0
    with pytest.raises(ValueError, match="weight must be non-negative"):
        _adapt(
            negative,
            role=RunRole.POSITIVE,
            subject_outcome=RunOutcome.GREEN,
        )

    zero_total = copy.deepcopy(verification)
    for check_result in zero_total["gate"]["checks"]:
        check_result["weight"] = 0.0
    with pytest.raises(ValueError, match="positive total weight"):
        _adapt(
            zero_total,
            role=RunRole.POSITIVE,
            subject_outcome=RunOutcome.GREEN,
        )

    invalid_threshold = copy.deepcopy(verification)
    invalid_threshold["gate"]["threshold"] = 1.1
    with pytest.raises(ValueError, match="threshold must be <= 1"):
        _adapt(
            invalid_threshold,
            role=RunRole.POSITIVE,
            subject_outcome=RunOutcome.GREEN,
        )


def test_adapter_rejects_zero_threshold_all_failed_completion_forgery() -> None:
    verification = _verify(
        {
            "threshold": 0.5,
            "expect": [{"event": "a", "op": "==", "count": 1}],
        },
        [
            {"event": "a", "_timestamp": 1},
            {"event": "a", "_timestamp": 2},
        ],
    )
    assert verification["gate"]["score"] == 0
    assert verification["gate"]["checks"][0]["passed"] is False

    forged = copy.deepcopy(verification)
    forged["ok"] = True
    forged["verdict"] = "present"
    forged["gate"]["threshold"] = 0.0
    forged["gate"]["ok"] = True
    with pytest.raises(ValueError, match="threshold must be > 0"):
        _adapt(
            forged,
            role=RunRole.POSITIVE,
            subject_outcome=RunOutcome.GREEN,
        )


def test_validator_rejects_negative_weights_and_out_of_range_aggregation() -> None:
    verification = _verify(
        {
            "threshold": 0.5,
            "expect": [{"event": "a"}, {"event": "b"}],
        },
        [{"event": "a", "_timestamp": 1}],
    )
    bundle = _adapt(
        verification,
        role=RunRole.POSITIVE,
        subject_outcome=RunOutcome.GREEN,
    )

    negative = copy.deepcopy(bundle.document)
    negative["checks"][0]["weight"] = "-1"
    assert "weight must be canonical non-negative decimal text" in _errors(negative)

    out_of_range = copy.deepcopy(bundle.document)
    out_of_range["aggregation"]["threshold"] = "1.1"
    assert "threshold in (0, 1]" in _errors(out_of_range)

    zero = copy.deepcopy(bundle.document)
    zero["aggregation"]["threshold"] = "0"
    assert "threshold in (0, 1]" in _errors(zero)


def test_declared_schema_is_mirrored_on_disk_exactly() -> None:
    path = ROOT / "docs" / "schema" / "ouroboros-gate-evidence-v1.schema.json"
    assert json.loads(path.read_text(encoding="utf-8")) == OUROBOROS_GATE_EVIDENCE_SCHEMA
