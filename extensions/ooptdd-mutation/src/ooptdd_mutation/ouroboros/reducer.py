"""Pure transition kernel for the bounded Ouroboros evidence cycle."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ooptdd.identity import Digest

from .model import (
    COMPLETION_EVIDENCE_TIERS,
    TERMINAL_PHASES,
    CycleSnapshot,
    Disposition,
    EffectClass,
    EffectIntent,
    EffectKind,
    EventKind,
    EventRecord,
    EvidenceTier,
    FindingDisposition,
    InterruptReason,
    MaterialLock,
    MonitorVerdict,
    ObservationVerdict,
    OracleBoundary,
    Phase,
    ProtocolEvent,
    RunEvidence,
    RunOutcome,
    RunRole,
    TransitionResult,
)


def _require_exact_keys(
    payload: dict[str, Any], required: set[str], optional: set[str] | None = None
) -> None:
    allowed = required | (optional or set())
    missing = required - set(payload)
    extra = set(payload) - allowed
    if missing or extra:
        raise ValueError(f"payload keys mismatch: missing={sorted(missing)}, extra={sorted(extra)}")


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if "\0" in value:
        raise ValueError(f"{label} must not contain NUL")
    return value


def _effect(
    event: ProtocolEvent,
    kind: EffectKind,
    payload: dict[str, Any],
    *,
    ordinal: int,
) -> EffectIntent:
    return EffectIntent.create(
        event,
        kind,
        EffectClass.IDEMPOTENT_WRITE,
        payload,
        ordinal=ordinal,
    )


def _rejected(snapshot: CycleSnapshot, event: ProtocolEvent, code: str) -> TransitionResult:
    audit = _effect(
        event,
        EffectKind.AUDIT_REJECTION,
        {
            "code": code,
            "cycle_id": snapshot.identity.cycle_id,
            "event": event.kind.value,
            "phase": snapshot.phase.value,
        },
        ordinal=0,
    )
    return TransitionResult(False, False, snapshot, (audit,), code)


def _commit(
    snapshot: CycleSnapshot,
    event: ProtocolEvent,
    to_phase: Phase,
    *,
    extra_effects: tuple[EffectIntent, ...] = (),
    consume_step: bool = True,
    **updates: Any,
) -> TransitionResult:
    record_effect = _effect(
        event,
        EffectKind.RECORD_TRANSITION,
        {
            "cycle_id": snapshot.identity.cycle_id,
            "event_intent_hash": event.intent_hash,
            "from_phase": snapshot.phase.value,
            "to_phase": to_phase.value,
        },
        ordinal=0,
    )
    emitted = (record_effect, *extra_effects)
    event_record = EventRecord(
        cycle_id=event.cycle_id,
        cycle_identity_sha256=event.cycle_identity_sha256,
        event_id=event.event_id,
        kind=event.kind,
        payload_json=event.payload_json,
        intent_hash=event.intent_hash,
        from_phase=snapshot.phase,
        to_phase=to_phase,
    )
    next_snapshot = replace(
        snapshot,
        phase=to_phase,
        revision=snapshot.revision + 1,
        steps_used=snapshot.steps_used + (1 if consume_step else 0),
        events=(*snapshot.events, event_record),
        effects=(*snapshot.effects, *emitted),
        **updates,
    )
    return TransitionResult(True, False, next_snapshot, emitted)


def _fault_transition(
    snapshot: CycleSnapshot,
    event: ProtocolEvent,
    to_phase: Phase,
    code: str,
    *,
    extra_effects: tuple[EffectIntent, ...] = (),
    consume_step: bool,
    **updates: Any,
) -> TransitionResult:
    """Journal a protocol fault as a real state transition, not a mutable rejection."""

    result = _commit(
        snapshot,
        event,
        to_phase,
        extra_effects=extra_effects,
        consume_step=consume_step,
        **updates,
    )
    return replace(result, rejection_code=code)


def _collision(snapshot: CycleSnapshot, event: ProtocolEvent) -> TransitionResult:
    reason = InterruptReason.IDENTITY_CONFLICT
    if snapshot.mutation_active:
        restore = _effect(
            event,
            EffectKind.RESTORE_REQUIRED,
            {"cycle_id": snapshot.identity.cycle_id, "reason": reason.value},
            ordinal=1,
        )
        return _fault_transition(
            snapshot,
            event,
            Phase.RECOVERY_REQUIRED,
            "event_identity_conflict",
            extra_effects=(restore,),
            consume_step=snapshot.steps_used < snapshot.budget.max_steps,
            pending_interrupt=reason,
        )
    return _fault_transition(
        snapshot,
        event,
        Phase.IDENTITY_CONFLICT,
        "event_identity_conflict",
        consume_step=snapshot.steps_used < snapshot.budget.max_steps,
        halt_reason=reason,
    )


def _budget_exhausted(snapshot: CycleSnapshot, event: ProtocolEvent) -> TransitionResult:
    reason = InterruptReason.BUDGET_EXHAUSTED
    if snapshot.mutation_active:
        restore = _effect(
            event,
            EffectKind.RESTORE_REQUIRED,
            {"cycle_id": snapshot.identity.cycle_id, "reason": reason.value},
            ordinal=1,
        )
        return _fault_transition(
            snapshot,
            event,
            Phase.RECOVERY_REQUIRED,
            reason.value,
            extra_effects=(restore,),
            consume_step=False,
            pending_interrupt=reason,
        )
    return _fault_transition(
        snapshot,
        event,
        Phase.INTERRUPTED,
        reason.value,
        consume_step=False,
        halt_reason=reason,
    )


def _parse_run(snapshot: CycleSnapshot, payload: dict[str, Any], role: RunRole) -> RunEvidence:
    _require_exact_keys(
        payload,
        {
            "run_id",
            "artifact_namespace",
            "outcome",
            "observation",
            "monitor",
            "evidence_tier",
            "artifact",
            "material_lock_sha256",
            "executed_source",
        },
    )
    if snapshot.material_lock is None:
        raise ValueError("run evidence requires locked materials")
    run = RunEvidence(
        role=role,
        run_id=_require_text(payload["run_id"], "run_id"),
        artifact_namespace=_require_text(payload["artifact_namespace"], "artifact_namespace"),
        outcome=RunOutcome(payload["outcome"]),
        observation=ObservationVerdict(payload["observation"]),
        monitor=MonitorVerdict(payload["monitor"]),
        evidence_tier=EvidenceTier(payload["evidence_tier"]),
        artifact=Digest.from_dict(payload["artifact"]),
        material_lock_sha256=_require_text(payload["material_lock_sha256"], "material_lock_sha256"),
        executed_source=Digest.from_dict(payload["executed_source"]),
    )
    if run.material_lock_sha256 != snapshot.material_lock.fingerprint:
        raise ValueError("run evidence was not produced under the current material lock")
    if run.run_id in {item.run_id for item in snapshot.runs}:
        raise ValueError("run_id must be unique across protocol phases")
    if run.artifact_namespace in {item.artifact_namespace for item in snapshot.runs}:
        raise ValueError("artifact_namespace must be unique across protocol phases")
    expected_source = snapshot.material_lock.source
    if role is RunRole.NEGATIVE:
        if snapshot.mutated_source is None:
            raise ValueError("negative evidence requires a mutated-source identity")
        expected_source = snapshot.mutated_source
    if run.executed_source != expected_source:
        raise ValueError(f"{role.value} evidence names the wrong executed source")

    inconclusive = (
        run.outcome is RunOutcome.INCONCLUSIVE or run.observation is ObservationVerdict.INCONCLUSIVE
    )
    expected_outcome = (
        RunOutcome.RED if role in {RunRole.INITIAL_RED, RunRole.NEGATIVE} else RunOutcome.GREEN
    )
    if not inconclusive and run.outcome is not expected_outcome:
        raise ValueError(f"{role.value} requires outcome {expected_outcome.value!r}")
    if (
        not inconclusive
        and role in {RunRole.POSITIVE, RunRole.REGREEN}
        and run.observation is not ObservationVerdict.PRESENT
    ):
        raise ValueError(f"{role.value} requires present readback observation")
    if (
        not inconclusive
        and role in {RunRole.POSITIVE, RunRole.REGREEN}
        and run.evidence_tier not in COMPLETION_EVIDENCE_TIERS
    ):
        raise ValueError(f"{role.value} must prove readback, not only local emission")
    if run.evidence_tier is EvidenceTier.EXTERNAL_VERDICT:
        if snapshot.oracle is None or not snapshot.oracle.corroborated:
            raise ValueError("external_verdict requires independently corroborated oracle evidence")
    return run


def _run_is_inconclusive(run: RunEvidence) -> bool:
    return (
        run.outcome is RunOutcome.INCONCLUSIVE or run.observation is ObservationVerdict.INCONCLUSIVE
    )


def _commit_run(
    snapshot: CycleSnapshot,
    event: ProtocolEvent,
    run: RunEvidence,
    success_phase: Phase,
) -> TransitionResult:
    runs = (*snapshot.runs, run)
    if not _run_is_inconclusive(run):
        return _commit(snapshot, event, success_phase, runs=runs)
    reason = InterruptReason.INCONCLUSIVE
    if snapshot.mutation_active:
        restore = _effect(
            event,
            EffectKind.RESTORE_REQUIRED,
            {"cycle_id": snapshot.identity.cycle_id, "reason": reason.value},
            ordinal=1,
        )
        return _commit(
            snapshot,
            event,
            Phase.RECOVERY_REQUIRED,
            extra_effects=(restore,),
            runs=runs,
            pending_interrupt=reason,
        )
    return _commit(snapshot, event, Phase.INCONCLUSIVE, runs=runs, halt_reason=reason)


def _restore(
    snapshot: CycleSnapshot, event: ProtocolEvent, payload: dict[str, Any]
) -> TransitionResult:
    _require_exact_keys(payload, {"restored_source"})
    if snapshot.material_lock is None or not snapshot.mutation_active:
        return _rejected(snapshot, event, "no_active_mutation")
    restored = Digest.from_dict(payload["restored_source"])
    if restored != snapshot.material_lock.source:
        return _rejected(snapshot, event, "restoration_does_not_match_locked_source")
    if (
        snapshot.phase
        in {
            Phase.MUTATION_ACTIVE,
            Phase.NEGATIVE_RED_CONFIRMED,
        }
        and snapshot.steps_used >= snapshot.budget.max_steps
    ):
        return _commit(
            snapshot,
            event,
            Phase.INTERRUPTED,
            consume_step=False,
            mutation_active=False,
            restored_source=restored,
            halt_reason=InterruptReason.BUDGET_EXHAUSTED,
        )
    if snapshot.phase is Phase.RECOVERY_REQUIRED:
        reason = snapshot.pending_interrupt
        if reason is InterruptReason.IDENTITY_CONFLICT:
            target = Phase.IDENTITY_CONFLICT
        elif reason is InterruptReason.INCONCLUSIVE:
            target = Phase.INCONCLUSIVE
        else:
            target = Phase.INTERRUPTED
        return _commit(
            snapshot,
            event,
            target,
            consume_step=snapshot.steps_used < snapshot.budget.max_steps,
            mutation_active=False,
            restored_source=restored,
            halt_reason=reason,
            pending_interrupt=None,
        )
    if snapshot.phase is not Phase.NEGATIVE_RED_CONFIRMED:
        return _rejected(snapshot, event, "restore_requires_negative_red")
    return _commit(
        snapshot,
        event,
        Phase.RESTORED,
        mutation_active=False,
        restored_source=restored,
    )


def _interrupt(
    snapshot: CycleSnapshot, event: ProtocolEvent, payload: dict[str, Any]
) -> TransitionResult:
    _require_exact_keys(payload, {"reason"})
    reason = InterruptReason(payload["reason"])
    if reason in {
        InterruptReason.BUDGET_EXHAUSTED,
        InterruptReason.IDENTITY_CONFLICT,
    }:
        raise ValueError(f"{reason.value} is reducer-owned and cannot be asserted")
    if snapshot.mutation_active:
        restore = _effect(
            event,
            EffectKind.RESTORE_REQUIRED,
            {"cycle_id": snapshot.identity.cycle_id, "reason": reason.value},
            ordinal=1,
        )
        return _commit(
            snapshot,
            event,
            Phase.RECOVERY_REQUIRED,
            extra_effects=(restore,),
            pending_interrupt=reason,
        )
    target = Phase.INCONCLUSIVE if reason is InterruptReason.INCONCLUSIVE else Phase.INTERRUPTED
    return _commit(snapshot, event, target, halt_reason=reason)


def step(snapshot: CycleSnapshot, event: ProtocolEvent) -> TransitionResult:
    """Reduce one typed event into an immutable snapshot and idempotent effect intents.

    Exact event replays return the original effect intents.  Reusing an event ID for a
    different intent is a protocol conflict.  Invalid ordering never mutates state.
    """

    if (
        event.cycle_id != snapshot.identity.cycle_id
        or event.cycle_identity_sha256 != snapshot.identity.fingerprint
    ):
        return _rejected(snapshot, event, "cycle_identity_mismatch")

    prior_id_records = tuple(item for item in snapshot.events if item.event_id == event.event_id)
    exact_record = next(
        (item for item in prior_id_records if item.intent_hash == event.intent_hash),
        None,
    )
    if exact_record is not None:
        replay_effects = tuple(
            effect
            for effect in snapshot.effects
            if effect.causation_event_id == event.event_id
            and effect.causation_intent_hash == event.intent_hash
        )
        return TransitionResult(True, True, snapshot, replay_effects)

    if snapshot.phase in TERMINAL_PHASES:
        return _rejected(snapshot, event, "terminal_state")

    if snapshot.phase is Phase.RECOVERY_REQUIRED:
        if event.kind is not EventKind.RESTORE:
            return _rejected(snapshot, event, "recovery_requires_restore")
        if prior_id_records:
            # Recovery has a two-record safety tail at most: fault handoff, then
            # restoration.  Re-journalling identity conflicts here would permit an
            # unbounded sequence of non-counted RECOVERY_REQUIRED transitions.
            return _rejected(snapshot, event, "event_identity_conflict_during_recovery")

    if prior_id_records:
        return _collision(snapshot, event)

    if snapshot.steps_used >= snapshot.budget.max_steps and not (
        snapshot.mutation_active and event.kind is EventKind.RESTORE
    ):
        return _budget_exhausted(snapshot, event)

    payload = event.payload
    try:
        if event.kind is EventKind.INTERRUPT:
            return _interrupt(snapshot, event, payload)

        if event.kind is EventKind.RESTORE and snapshot.phase in {
            Phase.MUTATION_ACTIVE,
            Phase.NEGATIVE_RED_CONFIRMED,
            Phase.RECOVERY_REQUIRED,
        }:
            return _restore(snapshot, event, payload)

        if snapshot.phase is Phase.INIT and event.kind is EventKind.SIZE:
            _require_exact_keys(payload, {"policy_version"})
            _require_text(payload["policy_version"], "policy_version")
            return _commit(snapshot, event, Phase.SIZED)

        if snapshot.phase is Phase.SIZED and event.kind is EventKind.LOCK:
            _require_exact_keys(payload, {"materials", "oracle"})
            materials = MaterialLock.from_dict(payload["materials"])
            oracle = OracleBoundary.from_dict(payload["oracle"])
            return _commit(
                snapshot,
                event,
                Phase.LOCKED,
                material_lock=materials,
                oracle=oracle,
            )

        if snapshot.phase is Phase.LOCKED and event.kind is EventKind.INITIAL_RED:
            run = _parse_run(snapshot, payload, RunRole.INITIAL_RED)
            return _commit_run(snapshot, event, run, Phase.INITIAL_RED_CONFIRMED)

        if snapshot.phase is Phase.INITIAL_RED_CONFIRMED and event.kind is EventKind.GREEN:
            run = _parse_run(snapshot, payload, RunRole.POSITIVE)
            return _commit_run(snapshot, event, run, Phase.GREEN_CONFIRMED)

        if snapshot.phase is Phase.GREEN_CONFIRMED and event.kind is EventKind.QUARANTINE:
            _require_exact_keys(payload, {"artifact_namespace", "quarantine"})
            namespace = _require_text(payload["artifact_namespace"], "artifact_namespace")
            if namespace in {item.artifact_namespace for item in snapshot.runs}:
                raise ValueError("quarantine namespace must be isolated from run namespaces")
            quarantine = Digest.from_dict(payload["quarantine"])
            return _commit(
                snapshot,
                event,
                Phase.QUARANTINED,
                quarantine=quarantine,
                quarantine_namespace=namespace,
            )

        if snapshot.phase is Phase.QUARANTINED and event.kind is EventKind.MUTATION_APPLIED:
            _require_exact_keys(payload, {"mutation_delta", "source_before", "source_after"})
            if snapshot.material_lock is None:
                raise ValueError("mutation requires locked materials")
            delta = Digest.from_dict(payload["mutation_delta"])
            before = Digest.from_dict(payload["source_before"])
            after = Digest.from_dict(payload["source_after"])
            if before != snapshot.material_lock.source:
                raise ValueError("mutation source_before must equal the locked source")
            if after == before:
                raise ValueError("mutation must change the source identity")
            return _commit(
                snapshot,
                event,
                Phase.MUTATION_ACTIVE,
                mutation_delta=delta,
                mutated_source=after,
                mutation_active=True,
            )

        if snapshot.phase is Phase.MUTATION_ACTIVE and event.kind is EventKind.NEGATIVE_RED:
            run = _parse_run(snapshot, payload, RunRole.NEGATIVE)
            return _commit_run(snapshot, event, run, Phase.NEGATIVE_RED_CONFIRMED)

        if snapshot.phase is Phase.RESTORED and event.kind is EventKind.REGREEN:
            run = _parse_run(snapshot, payload, RunRole.REGREEN)
            return _commit_run(snapshot, event, run, Phase.REGREEN_CONFIRMED)

        if snapshot.phase is Phase.REGREEN_CONFIRMED and event.kind is EventKind.ENUMERATE_FINDINGS:
            _require_exact_keys(payload, {"finding_ids"})
            finding_ids = payload["finding_ids"]
            if not isinstance(finding_ids, list):
                raise ValueError("finding_ids must be a list")
            for item in finding_ids:
                _require_text(item, "finding_id")
            if len(finding_ids) != len(set(finding_ids)):
                raise ValueError("finding_ids must be unique")
            return _commit(
                snapshot,
                event,
                Phase.BITE_PENDING,
                finding_ids=tuple(finding_ids),
            )

        if snapshot.phase is Phase.BITE_PENDING and event.kind is EventKind.DISPOSE_FINDING:
            _require_exact_keys(
                payload,
                {
                    "finding_id",
                    "disposition",
                    "bound_material_changed",
                    "change_evidence",
                },
            )
            finding_id = _require_text(payload["finding_id"], "finding_id")
            if finding_id not in snapshot.finding_ids:
                raise ValueError("finding was not declared by the Bite enumeration")
            if finding_id in {item.finding_id for item in snapshot.dispositions}:
                raise ValueError("a finding may be disposed exactly once")
            if not isinstance(payload["bound_material_changed"], bool):
                raise ValueError("bound_material_changed must be a boolean")
            disposition = Disposition(payload["disposition"])
            changed = payload["bound_material_changed"]
            change_evidence = (
                Digest.from_dict(payload["change_evidence"])
                if payload["change_evidence"] is not None
                else None
            )
            record = FindingDisposition(finding_id, disposition, changed, change_evidence)
            return _commit(
                snapshot,
                event,
                Phase.BITE_PENDING,
                dispositions=(*snapshot.dispositions, record),
                bound_material_changed=snapshot.bound_material_changed or changed,
            )

        if snapshot.phase is Phase.BITE_PENDING and event.kind is EventKind.SEAL:
            _require_exact_keys(payload, set(), {"successor_cycle_id"})
            if {item.finding_id for item in snapshot.dispositions} != set(snapshot.finding_ids):
                raise ValueError("every enumerated finding needs exactly one disposition")
            successor = payload.get("successor_cycle_id")
            if snapshot.bound_material_changed:
                successor = _require_text(successor, "successor_cycle_id")
                if successor == snapshot.identity.cycle_id:
                    raise ValueError("a successor cycle must have a distinct identity")
                if snapshot.identity.generation + 1 >= snapshot.budget.max_generations:
                    raise ValueError("generation budget leaves no room for the required successor")
                return _commit(
                    snapshot,
                    event,
                    Phase.SUPERSEDED_BY_SUCCESSOR,
                    successor_cycle_id=successor,
                )
            if successor is not None:
                raise ValueError("an unchanged cycle must not invent a successor")
            return _commit(snapshot, event, Phase.COMPLETE)
    except (KeyError, TypeError, ValueError) as error:
        return _rejected(snapshot, event, f"invalid_payload:{error}")

    return _rejected(snapshot, event, "invalid_transition")


__all__ = ("step",)
