from __future__ import annotations

import copy
import itertools
from dataclasses import FrozenInstanceError, replace

import pytest

from ooptdd_mutation.ouroboros import (
    CycleSnapshot,
    Disposition,
    EffectKind,
    EventKind,
    EvidenceTier,
    InterruptReason,
    MaterialLock,
    MonitorVerdict,
    ObservationVerdict,
    OracleBoundary,
    Phase,
    ProtocolBudget,
    ProtocolEvent,
    RunOutcome,
    digest_raw,
    receipt_content_digest,
    receipt_from_snapshot,
    step,
    successor_from_receipt,
    validate_receipt,
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


def _oracle(*, corroborated: bool = False) -> OracleBoundary:
    return OracleBoundary(
        emit_identity="sut://emitter",
        read_identity="store://reader" if corroborated else "sut://emitter",
        separate_source=corroborated,
        corroborated=corroborated,
    )


def _event(
    number: int,
    kind: EventKind,
    payload: dict,
    *,
    cycle_id: str = "cycle-0",
) -> ProtocolEvent:
    return ProtocolEvent.create(cycle_id, f"event-{number:02d}", kind, payload)


def _run_payload(
    role: str,
    materials: MaterialLock,
    *,
    evidence_tier: EvidenceTier = EvidenceTier.ARRIVED,
    executed_source=None,
    observation: ObservationVerdict | None = None,
    monitor: MonitorVerdict | None = None,
) -> dict:
    red = role in {"initial", "negative"}
    return {
        "run_id": f"run-{role}",
        "artifact_namespace": f"artifacts/{role}",
        "outcome": (RunOutcome.RED if red else RunOutcome.GREEN).value,
        "observation": (
            observation or (ObservationVerdict.ABSENT if red else ObservationVerdict.PRESENT)
        ).value,
        "monitor": (monitor or (MonitorVerdict.VIOL if red else MonitorVerdict.SAT)).value,
        "evidence_tier": evidence_tier.value,
        "artifact": _digest(f"receipt-{role}", "run-artifact").to_dict(),
        "material_lock_sha256": materials.fingerprint,
        "executed_source": (executed_source or materials.source).to_dict(),
    }


def _advance(snapshot: CycleSnapshot, event: ProtocolEvent) -> CycleSnapshot:
    result = step(snapshot, event)
    assert result.accepted, result.rejection_code
    assert not result.replayed
    return result.snapshot


def _through_regreen(
    *,
    budget: ProtocolBudget | None = None,
    corroborated: bool = False,
) -> tuple[CycleSnapshot, MaterialLock]:
    materials = _materials()
    snapshot = CycleSnapshot.start(
        "cycle-0", budget or ProtocolBudget(max_steps=20, max_generations=3)
    )
    snapshot = _advance(snapshot, _event(1, EventKind.SIZE, {"policy_version": "fixed-v1"}))
    mutated_source = _digest("mutated-source", "source")
    snapshot = _advance(
        snapshot,
        _event(
            2,
            EventKind.LOCK,
            {
                "materials": materials.to_dict(),
                "oracle": _oracle(corroborated=corroborated).to_dict(),
            },
        ),
    )
    snapshot = _advance(
        snapshot, _event(3, EventKind.INITIAL_RED, _run_payload("initial", materials))
    )
    positive_tier = EvidenceTier.EXTERNAL_VERDICT if corroborated else EvidenceTier.ARRIVED
    snapshot = _advance(
        snapshot,
        _event(
            4, EventKind.GREEN, _run_payload("positive", materials, evidence_tier=positive_tier)
        ),
    )
    snapshot = _advance(
        snapshot,
        _event(
            5,
            EventKind.QUARANTINE,
            {
                "artifact_namespace": "artifacts/quarantine",
                "quarantine": _digest("quarantine", "quarantine").to_dict(),
            },
        ),
    )
    snapshot = _advance(
        snapshot,
        _event(
            6,
            EventKind.MUTATION_APPLIED,
            {
                "mutation_delta": _digest("delta", "mutation").to_dict(),
                "source_before": materials.source.to_dict(),
                "source_after": mutated_source.to_dict(),
            },
        ),
    )
    snapshot = _advance(
        snapshot,
        _event(
            7,
            EventKind.NEGATIVE_RED,
            _run_payload("negative", materials, executed_source=mutated_source),
        ),
    )
    snapshot = _advance(
        snapshot,
        _event(8, EventKind.RESTORE, {"restored_source": materials.source.to_dict()}),
    )
    snapshot = _advance(
        snapshot,
        _event(
            9, EventKind.REGREEN, _run_payload("regreen", materials, evidence_tier=positive_tier)
        ),
    )
    assert snapshot.phase is Phase.REGREEN_CONFIRMED
    return snapshot, materials


def test_happy_cycle_seals_a_replay_validated_receipt():
    snapshot, _ = _through_regreen(corroborated=True)
    snapshot = _advance(snapshot, _event(10, EventKind.ENUMERATE_FINDINGS, {"finding_ids": []}))
    snapshot = _advance(snapshot, _event(11, EventKind.SEAL, {}))

    assert snapshot.phase is Phase.COMPLETE
    receipt = receipt_from_snapshot(snapshot, receipt_id="receipt-cycle-0")
    assert validate_receipt(receipt) == []
    assert receipt["status"] == "complete"
    assert len({run["run_id"] for run in receipt["runs"]}) == 4
    assert receipt["integrity"]["value"]

    tampered = copy.deepcopy(receipt)
    tampered["material_lock"]["source_commit"] = "f" * 40
    errors = "\n".join(validate_receipt(tampered))
    assert "integrity.value does not match" in errors
    assert "material_lock does not match the reducer-replayed trace" in errors

    relabelled = copy.deepcopy(receipt)
    relabelled["cycle"]["generation"] = 1
    relabelled["cycle"]["previous_receipt_sha256"] = "0" * 64
    relabelled["lineage"]["predecessor_receipt_sha256"] = "0" * 64
    relabelled["integrity"]["value"] = receipt_content_digest(
        relabelled, schema_version=relabelled["schema_version"]
    ).value
    assert "cycle_identity_mismatch" in "\n".join(validate_receipt(relabelled))


def test_terminal_snapshot_without_replayable_history_cannot_be_receipted():
    snapshot, _ = _through_regreen()
    snapshot = _advance(snapshot, _event(10, EventKind.ENUMERATE_FINDINGS, {"finding_ids": []}))
    snapshot = _advance(snapshot, _event(11, EventKind.SEAL, {}))
    forged = replace(snapshot, events=(), effects=(), revision=0, steps_used=0)
    with pytest.raises(ValueError, match="replayed trace ends"):
        receipt_from_snapshot(forged, receipt_id="forged")


def test_recomputed_self_hash_cannot_hide_an_illegal_trace():
    snapshot, _ = _through_regreen()
    snapshot = _advance(snapshot, _event(10, EventKind.ENUMERATE_FINDINGS, {"finding_ids": []}))
    snapshot = _advance(snapshot, _event(11, EventKind.SEAL, {}))
    receipt = receipt_from_snapshot(snapshot, receipt_id="receipt-cycle-0")
    receipt["trace"]["accepted_events"][3]["to_phase"] = Phase.LOCKED.value
    receipt["integrity"]["value"] = receipt_content_digest(
        receipt, schema_version=receipt["schema_version"]
    ).value
    errors = "\n".join(validate_receipt(receipt))
    assert "does not match reducer output" in errors


def test_bite_change_forces_a_distinct_successor_generation():
    snapshot, _ = _through_regreen()
    snapshot = _advance(
        snapshot,
        _event(10, EventKind.ENUMERATE_FINDINGS, {"finding_ids": ["finding-1"]}),
    )
    snapshot = _advance(
        snapshot,
        _event(
            11,
            EventKind.DISPOSE_FINDING,
            {
                "finding_id": "finding-1",
                "disposition": Disposition.FIXED.value,
                "bound_material_changed": True,
                "change_evidence": _digest("bite-fix", "change-evidence").to_dict(),
            },
        ),
    )

    rejected = step(snapshot, _event(12, EventKind.SEAL, {}))
    assert not rejected.accepted
    assert "successor_cycle_id" in rejected.rejection_code

    invalid_successor = step(
        snapshot,
        _event(13, EventKind.SEAL, {"successor_cycle_id": "cycle\0invalid"}),
    )
    assert not invalid_successor.accepted
    assert invalid_successor.snapshot == snapshot
    assert "must not contain NUL" in invalid_successor.rejection_code

    sealed = step(
        snapshot,
        _event(12, EventKind.SEAL, {"successor_cycle_id": "cycle-1"}),
    )
    assert sealed.accepted
    assert sealed.snapshot.phase is Phase.SUPERSEDED_BY_SUCCESSOR
    assert {effect.kind for effect in sealed.effects} == {EffectKind.RECORD_TRANSITION}
    receipt = receipt_from_snapshot(sealed.snapshot, receipt_id="receipt-cycle-0")
    assert validate_receipt(receipt) == []
    assert receipt["lineage"]["successor_cycle_id"] == "cycle-1"
    successor = successor_from_receipt(receipt)
    assert successor.identity.cycle_id == "cycle-1"
    assert successor.identity.generation == 1
    assert successor.identity.previous_receipt_sha256 == receipt["integrity"]["value"]

    class LineageSwap(dict):
        def __getitem__(self, key):
            if key == "lineage":
                return {
                    "predecessor_receipt_sha256": None,
                    "successor_cycle_id": "unvalidated-cycle",
                }
            return super().__getitem__(key)

    frozen_successor = successor_from_receipt(LineageSwap(receipt))
    assert frozen_successor.identity.cycle_id == "cycle-1"


def test_bound_material_change_requires_typed_change_evidence():
    snapshot, _ = _through_regreen()
    snapshot = _advance(
        snapshot,
        _event(10, EventKind.ENUMERATE_FINDINGS, {"finding_ids": ["finding-1"]}),
    )
    result = step(
        snapshot,
        _event(
            11,
            EventKind.DISPOSE_FINDING,
            {
                "finding_id": "finding-1",
                "disposition": Disposition.FIXED.value,
                "bound_material_changed": True,
                "change_evidence": None,
            },
        ),
    )
    assert not result.accepted
    assert result.snapshot == snapshot
    assert "change evidence" in result.rejection_code


def test_finding_identifiers_reject_nul_before_bite_state_is_mutated():
    snapshot, _ = _through_regreen()
    result = step(
        snapshot,
        _event(
            10,
            EventKind.ENUMERATE_FINDINGS,
            {"finding_ids": ["finding\0invalid"]},
        ),
    )
    assert not result.accepted
    assert result.snapshot == snapshot
    assert "must not contain NUL" in result.rejection_code


def test_oracle_boundary_requires_actual_boolean_flags():
    with pytest.raises(ValueError, match="must be booleans"):
        OracleBoundary(
            emit_identity="sut://emitter",
            read_identity="store://reader",
            separate_source=1,  # type: ignore[arg-type]
            corroborated=False,
        )


def test_conflicting_event_cannot_rewrite_a_sealed_snapshot():
    snapshot, _ = _through_regreen()
    snapshot = _advance(snapshot, _event(10, EventKind.ENUMERATE_FINDINGS, {"finding_ids": []}))
    sealed = _advance(snapshot, _event(11, EventKind.SEAL, {}))
    conflicting = ProtocolEvent.create(
        "cycle-0",
        "event-11",
        EventKind.SEAL,
        {"successor_cycle_id": "invented"},
    )
    result = step(sealed, conflicting)
    assert not result.accepted
    assert result.rejection_code == "terminal_state"
    assert result.snapshot == sealed


def test_invalid_order_is_observable_and_does_not_mutate_state():
    snapshot = CycleSnapshot.start("cycle-invalid", ProtocolBudget(max_steps=5, max_generations=1))
    result = step(snapshot, _event(1, EventKind.GREEN, {}, cycle_id="cycle-invalid"))
    assert not result.accepted
    assert result.snapshot == snapshot
    assert result.rejection_code == "invalid_transition"
    assert [effect.kind for effect in result.effects] == [EffectKind.AUDIT_REJECTION]


def test_exact_replay_reemits_stable_effect_ids_and_collision_fails_closed():
    snapshot = CycleSnapshot.start("cycle-replay", ProtocolBudget(max_steps=5, max_generations=1))
    event = _event(1, EventKind.SIZE, {"policy_version": "fixed-v1"}, cycle_id="cycle-replay")
    first = step(snapshot, event)
    replay = step(first.snapshot, event)
    assert replay.accepted and replay.replayed
    assert replay.snapshot is first.snapshot
    assert [effect.effect_id for effect in replay.effects] == [
        effect.effect_id for effect in first.effects
    ]

    collision = ProtocolEvent.create(
        "cycle-replay", event.event_id, EventKind.SIZE, {"policy_version": "other"}
    )
    conflict = step(first.snapshot, collision)
    assert conflict.accepted
    assert conflict.rejection_code == "event_identity_conflict"
    assert conflict.snapshot.phase is Phase.IDENTITY_CONFLICT
    replayed_conflict = step(conflict.snapshot, collision)
    assert replayed_conflict.accepted and replayed_conflict.replayed
    assert [effect.effect_id for effect in replayed_conflict.effects] == [
        effect.effect_id for effect in conflict.effects
    ]


def test_event_payload_and_snapshot_are_immutable():
    payload = {"policy_version": "fixed-v1"}
    event = _event(1, EventKind.SIZE, payload)
    payload["policy_version"] = "mutated"
    assert event.payload == {"policy_version": "fixed-v1"}
    event.payload["policy_version"] = "also-mutated"
    assert event.payload == {"policy_version": "fixed-v1"}
    with pytest.raises(FrozenInstanceError):
        event.event_id = "replacement"  # type: ignore[misc]


def test_event_identity_is_scoped_to_one_cycle():
    snapshot = CycleSnapshot.start("cycle-a", ProtocolBudget(max_steps=5, max_generations=1))
    foreign = ProtocolEvent.create(
        "cycle-b", "event-01", EventKind.SIZE, {"policy_version": "fixed-v1"}
    )
    result = step(snapshot, foreign)
    assert not result.accepted
    assert result.snapshot == snapshot
    assert result.rejection_code == "cycle_identity_mismatch"

    successor_event = ProtocolEvent.create(
        "cycle-a",
        "event-successor",
        EventKind.SIZE,
        {"policy_version": "fixed-v1"},
        generation=1,
        previous_receipt_sha256="0" * 64,
    )
    assert step(snapshot, successor_event).rejection_code == "cycle_identity_mismatch"


@pytest.mark.parametrize("field", ["cycle_id", "event_id"])
def test_protocol_event_identifiers_reject_nul(field: str):
    values = {"cycle_id": "cycle-0", "event_id": "event-0"}
    values[field] += "\0suffix"
    with pytest.raises(ValueError, match="must not contain NUL"):
        ProtocolEvent.create(
            values["cycle_id"],
            values["event_id"],
            EventKind.SIZE,
            {"policy_version": "fixed-v1"},
        )


@pytest.mark.parametrize("diagnostic_monitor", [MonitorVerdict.VIOL, MonitorVerdict.PEND])
def test_positive_run_requires_present_readback_but_not_a_scalar_monitor_verdict(
    diagnostic_monitor: MonitorVerdict,
):
    cycle_id = "cycle-semantics"
    materials = _materials()
    snapshot = CycleSnapshot.start(cycle_id, ProtocolBudget(max_steps=6, max_generations=1))
    snapshot = _advance(
        snapshot,
        _event(
            1,
            EventKind.SIZE,
            {"policy_version": "fixed-v1"},
            cycle_id=cycle_id,
        ),
    )
    snapshot = _advance(
        snapshot,
        _event(
            2,
            EventKind.LOCK,
            {"materials": materials.to_dict(), "oracle": _oracle().to_dict()},
            cycle_id=cycle_id,
        ),
    )
    snapshot = _advance(
        snapshot,
        _event(
            3,
            EventKind.INITIAL_RED,
            _run_payload(
                "initial",
                materials,
                observation=ObservationVerdict.PRESENT,
                monitor=MonitorVerdict.VIOL,
            ),
            cycle_id=cycle_id,
        ),
    )
    absent = step(
        snapshot,
        _event(
            4,
            EventKind.GREEN,
            _run_payload(
                "positive",
                materials,
                observation=ObservationVerdict.ABSENT,
                monitor=diagnostic_monitor,
            ),
            cycle_id=cycle_id,
        ),
    )
    assert not absent.accepted
    assert absent.snapshot == snapshot
    assert "requires present readback observation" in absent.rejection_code

    present = step(
        snapshot,
        _event(
            4,
            EventKind.GREEN,
            _run_payload(
                "positive",
                materials,
                observation=ObservationVerdict.PRESENT,
                monitor=diagnostic_monitor,
            ),
            cycle_id=cycle_id,
        ),
    )
    assert present.accepted
    assert present.snapshot.phase is Phase.GREEN_CONFIRMED


def test_interrupt_during_mutation_requires_restoration_before_terminal_state():
    _, materials = _through_regreen()
    # Rebuild only through active mutation to exercise the safety path.
    active = CycleSnapshot.start("cycle-interrupt", ProtocolBudget(max_steps=20, max_generations=2))
    active = _advance(
        active,
        _event(
            21,
            EventKind.SIZE,
            {"policy_version": "fixed-v1"},
            cycle_id="cycle-interrupt",
        ),
    )
    active = _advance(
        active,
        _event(
            22,
            EventKind.LOCK,
            {"materials": materials.to_dict(), "oracle": _oracle().to_dict()},
            cycle_id="cycle-interrupt",
        ),
    )
    active = _advance(
        active,
        _event(
            23,
            EventKind.INITIAL_RED,
            _run_payload("initial", materials),
            cycle_id="cycle-interrupt",
        ),
    )
    active = _advance(
        active,
        _event(
            24,
            EventKind.GREEN,
            _run_payload("positive", materials),
            cycle_id="cycle-interrupt",
        ),
    )
    active = _advance(
        active,
        _event(
            25,
            EventKind.QUARANTINE,
            {
                "artifact_namespace": "artifacts/quarantine-2",
                "quarantine": _digest("quarantine-2", "quarantine").to_dict(),
            },
            cycle_id="cycle-interrupt",
        ),
    )
    active = _advance(
        active,
        _event(
            26,
            EventKind.MUTATION_APPLIED,
            {
                "mutation_delta": _digest("delta-2", "mutation").to_dict(),
                "source_before": materials.source.to_dict(),
                "source_after": _digest("mutated-source-2", "source").to_dict(),
            },
            cycle_id="cycle-interrupt",
        ),
    )
    interrupted = step(
        active,
        _event(
            27,
            EventKind.INTERRUPT,
            {"reason": InterruptReason.CANCELLED.value},
            cycle_id="cycle-interrupt",
        ),
    )
    assert interrupted.accepted
    assert interrupted.snapshot.phase is Phase.RECOVERY_REQUIRED
    assert interrupted.snapshot.mutation_active
    assert EffectKind.RESTORE_REQUIRED in {effect.kind for effect in interrupted.effects}

    restored = step(
        interrupted.snapshot,
        _event(
            28,
            EventKind.RESTORE,
            {"restored_source": materials.source.to_dict()},
            cycle_id="cycle-interrupt",
        ),
    )
    assert restored.accepted
    assert restored.snapshot.phase is Phase.INTERRUPTED
    assert not restored.snapshot.mutation_active


def test_negative_run_is_bound_to_the_mutated_source_and_preserves_inconclusive_evidence():
    completed, materials = _through_regreen()
    active = CycleSnapshot.start("cycle-0", completed.budget)
    for record in completed.events[:6]:
        active = _advance(active, record.event)
    assert active.phase is Phase.MUTATION_ACTIVE
    assert active.mutated_source is not None

    wrong_source = step(
        active,
        _event(70, EventKind.NEGATIVE_RED, _run_payload("negative-wrong", materials)),
    )
    assert not wrong_source.accepted
    assert wrong_source.snapshot == active
    assert "wrong executed source" in wrong_source.rejection_code

    inconclusive_payload = _run_payload(
        "negative-inconclusive",
        materials,
        evidence_tier=EvidenceTier.EMITTED,
        executed_source=active.mutated_source,
        observation=ObservationVerdict.INCONCLUSIVE,
        monitor=MonitorVerdict.PEND,
    )
    inconclusive_payload["outcome"] = RunOutcome.INCONCLUSIVE.value
    inconclusive = step(
        active,
        _event(71, EventKind.NEGATIVE_RED, inconclusive_payload),
    )
    assert inconclusive.accepted
    assert inconclusive.snapshot.phase is Phase.RECOVERY_REQUIRED
    assert inconclusive.snapshot.runs[-1].outcome is RunOutcome.INCONCLUSIVE
    assert EffectKind.RESTORE_REQUIRED in {effect.kind for effect in inconclusive.effects}

    restored = step(
        inconclusive.snapshot,
        _event(
            72,
            EventKind.RESTORE,
            {"restored_source": materials.source.to_dict()},
        ),
    )
    assert restored.accepted
    assert restored.snapshot.phase is Phase.INCONCLUSIVE
    assert not restored.snapshot.mutation_active


def test_budget_exhaustion_reserves_a_non_counted_restoration_step():
    materials = _materials()
    snapshot = CycleSnapshot.start("cycle-budget", ProtocolBudget(max_steps=6, max_generations=2))
    snapshot = _advance(
        snapshot,
        _event(
            1,
            EventKind.SIZE,
            {"policy_version": "fixed-v1"},
            cycle_id="cycle-budget",
        ),
    )
    snapshot = _advance(
        snapshot,
        _event(
            2,
            EventKind.LOCK,
            {"materials": materials.to_dict(), "oracle": _oracle().to_dict()},
            cycle_id="cycle-budget",
        ),
    )
    snapshot = _advance(
        snapshot,
        _event(
            3,
            EventKind.INITIAL_RED,
            _run_payload("initial", materials),
            cycle_id="cycle-budget",
        ),
    )
    snapshot = _advance(
        snapshot,
        _event(
            4,
            EventKind.GREEN,
            _run_payload("positive", materials),
            cycle_id="cycle-budget",
        ),
    )
    snapshot = _advance(
        snapshot,
        _event(
            5,
            EventKind.QUARANTINE,
            {
                "artifact_namespace": "artifacts/quarantine",
                "quarantine": _digest("quarantine", "quarantine").to_dict(),
            },
            cycle_id="cycle-budget",
        ),
    )
    mutated = _digest("mutated", "source")
    snapshot = _advance(
        snapshot,
        _event(
            6,
            EventKind.MUTATION_APPLIED,
            {
                "mutation_delta": _digest("delta", "mutation").to_dict(),
                "source_before": materials.source.to_dict(),
                "source_after": mutated.to_dict(),
            },
            cycle_id="cycle-budget",
        ),
    )
    direct_restore = step(
        snapshot,
        _event(
            9,
            EventKind.RESTORE,
            {"restored_source": materials.source.to_dict()},
            cycle_id="cycle-budget",
        ),
    )
    assert direct_restore.accepted
    assert direct_restore.snapshot.phase is Phase.INTERRUPTED
    assert direct_restore.snapshot.halt_reason is InterruptReason.BUDGET_EXHAUSTED
    assert not direct_restore.snapshot.mutation_active

    exhausted = step(
        snapshot,
        _event(
            7,
            EventKind.NEGATIVE_RED,
            _run_payload("negative", materials, executed_source=mutated),
            cycle_id="cycle-budget",
        ),
    )
    assert exhausted.accepted
    assert exhausted.rejection_code == InterruptReason.BUDGET_EXHAUSTED.value
    assert exhausted.snapshot.phase is Phase.RECOVERY_REQUIRED
    assert exhausted.snapshot.pending_interrupt is InterruptReason.BUDGET_EXHAUSTED

    ignored_during_recovery = step(
        exhausted.snapshot,
        _event(10, EventKind.GREEN, {}, cycle_id="cycle-budget"),
    )
    assert not ignored_during_recovery.accepted
    assert ignored_during_recovery.rejection_code == "recovery_requires_restore"
    assert ignored_during_recovery.snapshot == exhausted.snapshot

    recovery_snapshot = exhausted.snapshot
    for index in range(20):
        conflicting_restore = ProtocolEvent.create(
            "cycle-budget",
            "event-01",
            EventKind.RESTORE,
            {"restored_source": _digest(f"wrong-{index}", "source").to_dict()},
        )
        rejected = step(recovery_snapshot, conflicting_restore)
        assert not rejected.accepted
        assert rejected.rejection_code == "event_identity_conflict_during_recovery"
        assert rejected.snapshot == recovery_snapshot
    assert len(recovery_snapshot.events) <= recovery_snapshot.budget.max_steps + 1

    restored = step(
        exhausted.snapshot,
        _event(
            8,
            EventKind.RESTORE,
            {"restored_source": materials.source.to_dict()},
            cycle_id="cycle-budget",
        ),
    )
    assert restored.accepted
    assert restored.snapshot.phase is Phase.INTERRUPTED
    assert restored.snapshot.steps_used == snapshot.budget.max_steps
    assert len(restored.snapshot.events) <= restored.snapshot.budget.max_steps + 2


@pytest.mark.parametrize(
    "reason",
    [InterruptReason.BUDGET_EXHAUSTED, InterruptReason.IDENTITY_CONFLICT],
)
def test_interrupt_cannot_assert_reducer_owned_faults(reason: InterruptReason):
    snapshot = CycleSnapshot.start(
        "cycle-fault-reason", ProtocolBudget(max_steps=3, max_generations=1)
    )
    result = step(
        snapshot,
        _event(
            1,
            EventKind.INTERRUPT,
            {"reason": reason.value},
            cycle_id="cycle-fault-reason",
        ),
    )
    assert not result.accepted
    assert result.snapshot == snapshot
    assert "reducer-owned" in result.rejection_code


def test_restoration_remains_available_when_negative_red_consumes_final_step():
    materials = _materials()
    mutated = _digest("mutated-at-limit", "source")
    snapshot = CycleSnapshot.start(
        "cycle-final-negative",
        ProtocolBudget(max_steps=7, max_generations=1),
    )
    events = (
        _event(
            1,
            EventKind.SIZE,
            {"policy_version": "fixed-v1"},
            cycle_id="cycle-final-negative",
        ),
        _event(
            2,
            EventKind.LOCK,
            {"materials": materials.to_dict(), "oracle": _oracle().to_dict()},
            cycle_id="cycle-final-negative",
        ),
        _event(
            3,
            EventKind.INITIAL_RED,
            _run_payload("initial", materials),
            cycle_id="cycle-final-negative",
        ),
        _event(
            4,
            EventKind.GREEN,
            _run_payload("positive", materials),
            cycle_id="cycle-final-negative",
        ),
        _event(
            5,
            EventKind.QUARANTINE,
            {
                "artifact_namespace": "artifacts/quarantine",
                "quarantine": _digest("quarantine", "quarantine").to_dict(),
            },
            cycle_id="cycle-final-negative",
        ),
        _event(
            6,
            EventKind.MUTATION_APPLIED,
            {
                "mutation_delta": _digest("delta", "mutation").to_dict(),
                "source_before": materials.source.to_dict(),
                "source_after": mutated.to_dict(),
            },
            cycle_id="cycle-final-negative",
        ),
        _event(
            7,
            EventKind.NEGATIVE_RED,
            _run_payload("negative", materials, executed_source=mutated),
            cycle_id="cycle-final-negative",
        ),
    )
    for event in events:
        snapshot = _advance(snapshot, event)

    assert snapshot.phase is Phase.NEGATIVE_RED_CONFIRMED
    assert snapshot.steps_used == snapshot.budget.max_steps
    restored = step(
        snapshot,
        _event(
            8,
            EventKind.RESTORE,
            {"restored_source": materials.source.to_dict()},
            cycle_id="cycle-final-negative",
        ),
    )
    assert restored.accepted
    assert restored.snapshot.phase is Phase.INTERRUPTED
    assert restored.snapshot.halt_reason is InterruptReason.BUDGET_EXHAUSTED
    assert restored.snapshot.steps_used == snapshot.budget.max_steps
    assert not restored.snapshot.mutation_active
    assert len(restored.snapshot.events) == snapshot.budget.max_steps + 1


def test_external_verdict_is_rejected_without_independent_corroboration():
    materials = _materials()
    snapshot = CycleSnapshot.start("cycle-oracle", ProtocolBudget(max_steps=10, max_generations=1))
    snapshot = _advance(
        snapshot,
        _event(
            1,
            EventKind.SIZE,
            {"policy_version": "fixed-v1"},
            cycle_id="cycle-oracle",
        ),
    )
    snapshot = _advance(
        snapshot,
        _event(
            2,
            EventKind.LOCK,
            {"materials": materials.to_dict(), "oracle": _oracle().to_dict()},
            cycle_id="cycle-oracle",
        ),
    )
    snapshot = _advance(
        snapshot,
        _event(
            3,
            EventKind.INITIAL_RED,
            _run_payload("initial", materials),
            cycle_id="cycle-oracle",
        ),
    )
    result = step(
        snapshot,
        _event(
            4,
            EventKind.GREEN,
            _run_payload("positive", materials, evidence_tier=EvidenceTier.EXTERNAL_VERDICT),
            cycle_id="cycle-oracle",
        ),
    )
    assert not result.accepted
    assert "external_verdict" in result.rejection_code


def test_identity_collision_during_mutation_restores_before_conflict_terminal():
    materials = _materials()
    snapshot = CycleSnapshot.start(
        "cycle-mutation-conflict",
        ProtocolBudget(max_steps=20, max_generations=2),
    )
    events = [
        _event(
            1,
            EventKind.SIZE,
            {"policy_version": "fixed-v1"},
            cycle_id="cycle-mutation-conflict",
        ),
        _event(
            2,
            EventKind.LOCK,
            {"materials": materials.to_dict(), "oracle": _oracle().to_dict()},
            cycle_id="cycle-mutation-conflict",
        ),
        _event(
            3,
            EventKind.INITIAL_RED,
            _run_payload("initial", materials),
            cycle_id="cycle-mutation-conflict",
        ),
        _event(
            4,
            EventKind.GREEN,
            _run_payload("positive", materials),
            cycle_id="cycle-mutation-conflict",
        ),
        _event(
            5,
            EventKind.QUARANTINE,
            {
                "artifact_namespace": "artifacts/quarantine",
                "quarantine": _digest("quarantine", "quarantine").to_dict(),
            },
            cycle_id="cycle-mutation-conflict",
        ),
        _event(
            6,
            EventKind.MUTATION_APPLIED,
            {
                "mutation_delta": _digest("delta", "mutation").to_dict(),
                "source_before": materials.source.to_dict(),
                "source_after": _digest("mutated", "source").to_dict(),
            },
            cycle_id="cycle-mutation-conflict",
        ),
    ]
    for event in events:
        snapshot = _advance(snapshot, event)
    collision = ProtocolEvent.create(
        "cycle-mutation-conflict",
        "event-06",
        EventKind.MUTATION_APPLIED,
        {
            "mutation_delta": _digest("different", "mutation").to_dict(),
            "source_before": materials.source.to_dict(),
            "source_after": _digest("different-mutated", "source").to_dict(),
        },
    )
    result = step(snapshot, collision)
    assert result.accepted
    assert result.rejection_code == "event_identity_conflict"
    assert result.snapshot.phase is Phase.RECOVERY_REQUIRED
    assert result.snapshot.pending_interrupt is InterruptReason.IDENTITY_CONFLICT
    restored = step(
        result.snapshot,
        _event(
            7,
            EventKind.RESTORE,
            {"restored_source": materials.source.to_dict()},
            cycle_id="cycle-mutation-conflict",
        ),
    )
    assert restored.snapshot.phase is Phase.IDENTITY_CONFLICT
    assert not restored.snapshot.mutation_active


def test_bounded_short_sequences_preserve_totality_and_terminal_safety():
    budget = ProtocolBudget(max_steps=8, max_generations=2)
    choices = (
        (EventKind.SIZE, {"policy_version": "fixed-v1"}),
        (EventKind.GREEN, {}),
        (EventKind.SEAL, {}),
        (EventKind.INTERRUPT, {"reason": InterruptReason.CANCELLED.value}),
    )
    for sequence in itertools.product(choices, repeat=3):
        snapshot = CycleSnapshot.start("cycle-property", budget)
        for index, (kind, payload) in enumerate(sequence):
            event = ProtocolEvent.create("cycle-property", f"property-{index}", kind, payload)
            assert step(snapshot, event) == step(snapshot, event)
            result = step(snapshot, event)
            if result.accepted:
                snapshot = result.snapshot
            else:
                assert result.snapshot == snapshot
            assert snapshot.steps_used <= budget.max_steps
            if snapshot.phase in {
                Phase.COMPLETE,
                Phase.SUPERSEDED_BY_SUCCESSOR,
                Phase.INCONCLUSIVE,
                Phase.INTERRUPTED,
                Phase.IDENTITY_CONFLICT,
            }:
                assert not snapshot.mutation_active
