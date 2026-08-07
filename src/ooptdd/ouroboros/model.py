"""Immutable protocol values for one OOPTDD Ouroboros generation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .identity import (
    MAX_INTEROPERABLE_INTEGER,
    Digest,
    canonical_json_bytes,
    digest_json,
)

PROTOCOL_VERSION = "ooptdd-ouroboros-protocol/v2"
RECEIPT_VERSION = "ooptdd-ouroboros-receipt/v2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class Phase(str, Enum):
    INIT = "init"
    SIZED = "sized"
    LOCKED = "locked"
    INITIAL_RED_CONFIRMED = "initial_red_confirmed"
    GREEN_CONFIRMED = "green_confirmed"
    QUARANTINED = "quarantined"
    MUTATION_ACTIVE = "mutation_active"
    NEGATIVE_RED_CONFIRMED = "negative_red_confirmed"
    RESTORED = "restored"
    REGREEN_CONFIRMED = "regreen_confirmed"
    BITE_PENDING = "bite_pending"
    RECOVERY_REQUIRED = "recovery_required"
    COMPLETE = "complete"
    SUPERSEDED_BY_SUCCESSOR = "superseded_by_successor"
    INCONCLUSIVE = "inconclusive"
    INTERRUPTED = "interrupted"
    IDENTITY_CONFLICT = "identity_conflict"


TERMINAL_PHASES = frozenset(
    {
        Phase.COMPLETE,
        Phase.SUPERSEDED_BY_SUCCESSOR,
        Phase.INCONCLUSIVE,
        Phase.INTERRUPTED,
        Phase.IDENTITY_CONFLICT,
    }
)


class EventKind(str, Enum):
    SIZE = "size"
    LOCK = "lock"
    INITIAL_RED = "initial_red"
    GREEN = "green"
    QUARANTINE = "quarantine"
    MUTATION_APPLIED = "mutation_applied"
    NEGATIVE_RED = "negative_red"
    RESTORE = "restore"
    REGREEN = "regreen"
    ENUMERATE_FINDINGS = "enumerate_findings"
    DISPOSE_FINDING = "dispose_finding"
    SEAL = "seal"
    INTERRUPT = "interrupt"


class RunRole(str, Enum):
    INITIAL_RED = "initial_red"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    REGREEN = "regreen"


class RunOutcome(str, Enum):
    RED = "red"
    GREEN = "green"
    INCONCLUSIVE = "inconclusive"


class ObservationVerdict(str, Enum):
    """What an evidence source observed; intentionally not an FSM monitor value."""

    PRESENT = "present"
    ABSENT = "absent"
    INCONCLUSIVE = "inconclusive"


class MonitorVerdict(str, Enum):
    """One caller-selected monitor value, preserved as non-authoritative diagnostics."""

    SAT = "sat"
    VIOL = "viol"
    PEND = "pend"


class EvidenceTier(str, Enum):
    LOCAL_PASS = "local_pass"
    EMITTED = "emitted"
    ARRIVED = "arrived"
    QUERYABLE_CAUSAL = "queryable_causal"
    EXTERNAL_VERDICT = "external_verdict"


COMPLETION_EVIDENCE_TIERS = frozenset(
    {EvidenceTier.ARRIVED, EvidenceTier.QUERYABLE_CAUSAL, EvidenceTier.EXTERNAL_VERDICT}
)


class Disposition(str, Enum):
    FIXED = "fixed"
    DEFERRED = "deferred"
    REFUTED = "refuted"
    ACCEPTED_RISK = "accepted_risk"


class InterruptReason(str, Enum):
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    BUDGET_EXHAUSTED = "budget_exhausted"
    RETRY_EXHAUSTED = "retry_exhausted"
    INCONCLUSIVE = "inconclusive"
    IDENTITY_CONFLICT = "identity_conflict"


class EffectClass(str, Enum):
    READ = "read"
    IDEMPOTENT_WRITE = "idempotent_write"
    GUARDED_NON_IDEMPOTENT = "guarded_non_idempotent"


class EffectKind(str, Enum):
    RECORD_TRANSITION = "record_transition"
    AUDIT_REJECTION = "audit_rejection"
    RESTORE_REQUIRED = "restore_required"


def _require_text(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if "\0" in value:
        raise ValueError(f"{label} must not contain NUL")


def _json_object(value: str, label: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must be valid JSON: {error}") from error
    if not isinstance(decoded, dict):
        raise ValueError(f"{label} must encode an object")
    return decoded


@dataclass(frozen=True)
class ProtocolBudget:
    max_steps: int
    max_generations: int

    def __post_init__(self) -> None:
        for name in ("max_steps", "max_generations"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"budget {name} must be a positive integer")
        if self.max_steps > MAX_INTEROPERABLE_INTEGER - 2:
            raise ValueError("budget max_steps must reserve two interoperable safety-tail values")
        if self.max_generations > MAX_INTEROPERABLE_INTEGER:
            raise ValueError("budget max_generations exceeds the interoperable JSON range")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_steps": self.max_steps,
            "max_generations": self.max_generations,
        }


@dataclass(frozen=True)
class CycleIdentity:
    cycle_id: str
    generation: int = 0
    previous_receipt_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.cycle_id, "cycle_id")
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 0
        ):
            raise ValueError("generation must be a non-negative integer")
        if self.generation >= MAX_INTEROPERABLE_INTEGER:
            raise ValueError("generation leaves no interoperable successor-budget value")
        previous = self.previous_receipt_sha256
        if self.generation == 0 and previous is not None:
            raise ValueError("generation zero cannot name a predecessor receipt")
        if self.generation > 0 and (
            not isinstance(previous, str) or _SHA256_RE.fullmatch(previous) is None
        ):
            raise ValueError(
                "a successor generation requires a lowercase 64-hex predecessor receipt"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "generation": self.generation,
            "previous_receipt_sha256": self.previous_receipt_sha256,
        }

    @property
    def fingerprint(self) -> str:
        """Bind an event to the full generation identity, not only its display ID."""

        return digest_json(
            self.to_dict(),
            scope="ouroboros-cycle-identity",
            schema_version=PROTOCOL_VERSION,
        ).value


@dataclass(frozen=True)
class OracleBoundary:
    emit_identity: str
    read_identity: str
    separate_source: bool
    corroborated: bool

    def __post_init__(self) -> None:
        _require_text(self.emit_identity, "emit_identity")
        _require_text(self.read_identity, "read_identity")
        if not isinstance(self.separate_source, bool) or not isinstance(self.corroborated, bool):
            raise ValueError("oracle source flags must be booleans")
        if self.corroborated and not self.is_independent:
            raise ValueError("corroboration requires a distinct, separate read authority")

    @property
    def is_independent(self) -> bool:
        return self.separate_source and self.emit_identity != self.read_identity

    def to_dict(self) -> dict[str, Any]:
        return {
            "emit_identity": self.emit_identity,
            "read_identity": self.read_identity,
            "separate_source": self.separate_source,
            "corroborated": self.corroborated,
        }

    @classmethod
    def from_dict(cls, value: Any) -> OracleBoundary:
        if not isinstance(value, dict):
            raise ValueError("oracle must be an object")
        expected = {"emit_identity", "read_identity", "separate_source", "corroborated"}
        if set(value) != expected:
            raise ValueError(f"oracle fields must be exactly {sorted(expected)}")
        if not isinstance(value["separate_source"], bool) or not isinstance(
            value["corroborated"], bool
        ):
            raise ValueError("oracle source flags must be booleans")
        return cls(**value)


@dataclass(frozen=True)
class MaterialLock:
    spec: Digest
    verifier: Digest
    source: Digest
    environment: Digest
    source_commit: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_commit, str)
            or _GIT_OBJECT_RE.fullmatch(self.source_commit) is None
        ):
            raise ValueError("source_commit must be a lowercase 40- or 64-hex Git object ID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "verifier": self.verifier.to_dict(),
            "source": self.source.to_dict(),
            "environment": self.environment.to_dict(),
            "source_commit": self.source_commit,
        }

    @property
    def fingerprint(self) -> str:
        return digest_json(
            self.to_dict(), scope="ouroboros-material-lock", schema_version=PROTOCOL_VERSION
        ).value

    @classmethod
    def from_dict(cls, value: Any) -> MaterialLock:
        if not isinstance(value, dict):
            raise ValueError("materials must be an object")
        expected = {"spec", "verifier", "source", "environment", "source_commit"}
        if set(value) != expected:
            raise ValueError(f"material fields must be exactly {sorted(expected)}")
        return cls(
            spec=Digest.from_dict(value["spec"]),
            verifier=Digest.from_dict(value["verifier"]),
            source=Digest.from_dict(value["source"]),
            environment=Digest.from_dict(value["environment"]),
            source_commit=value["source_commit"],
        )


@dataclass(frozen=True)
class ProtocolEvent:
    cycle_id: str
    cycle_identity_sha256: str
    event_id: str
    kind: EventKind
    payload_json: str
    intent_hash: str

    def __post_init__(self) -> None:
        _require_text(self.cycle_id, "cycle_id")
        _require_text(self.event_id, "event_id")
        if _SHA256_RE.fullmatch(self.cycle_identity_sha256) is None:
            raise ValueError("cycle_identity_sha256 must be lowercase 64-hex")
        try:
            payload = json.loads(self.payload_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"payload_json must be valid JSON: {error}") from error
        if not isinstance(payload, dict):
            raise ValueError("event payload must be an object")
        canonical_payload = canonical_json_bytes(payload).decode("utf-8")
        if canonical_payload != self.payload_json:
            raise ValueError("payload_json must already be canonical")
        expected = digest_json(
            {
                "cycle_id": self.cycle_id,
                "cycle_identity_sha256": self.cycle_identity_sha256,
                "event_id": self.event_id,
                "kind": self.kind.value,
                "payload": payload,
            },
            scope="ouroboros-event-intent",
            schema_version=PROTOCOL_VERSION,
        ).value
        if self.intent_hash != expected:
            raise ValueError("event intent_hash does not match its content")

    @property
    def payload(self) -> dict[str, Any]:
        return _json_object(self.payload_json, "event payload_json")

    @classmethod
    def create(
        cls,
        cycle_id: str,
        event_id: str,
        kind: EventKind,
        payload: dict[str, Any],
        *,
        generation: int = 0,
        previous_receipt_sha256: str | None = None,
    ) -> ProtocolEvent:
        identity = CycleIdentity(cycle_id, generation, previous_receipt_sha256)
        _require_text(event_id, "event_id")
        if not isinstance(kind, EventKind):
            raise TypeError("kind must be an EventKind")
        payload_json = canonical_json_bytes(payload).decode("utf-8")
        value = json.loads(payload_json)
        intent_hash = digest_json(
            {
                "cycle_id": cycle_id,
                "cycle_identity_sha256": identity.fingerprint,
                "event_id": event_id,
                "kind": kind.value,
                "payload": value,
            },
            scope="ouroboros-event-intent",
            schema_version=PROTOCOL_VERSION,
        ).value
        return cls(
            cycle_id=cycle_id,
            cycle_identity_sha256=identity.fingerprint,
            event_id=event_id,
            kind=kind,
            payload_json=payload_json,
            intent_hash=intent_hash,
        )


@dataclass(frozen=True)
class EventRecord:
    cycle_id: str
    cycle_identity_sha256: str
    event_id: str
    kind: EventKind
    payload_json: str
    intent_hash: str
    from_phase: Phase
    to_phase: Phase

    def to_dict(self) -> dict[str, str]:
        return {
            "cycle_id": self.cycle_id,
            "cycle_identity_sha256": self.cycle_identity_sha256,
            "event_id": self.event_id,
            "kind": self.kind.value,
            "payload_json": self.payload_json,
            "intent_hash": self.intent_hash,
            "from_phase": self.from_phase.value,
            "to_phase": self.to_phase.value,
        }

    @classmethod
    def from_dict(cls, value: Any) -> EventRecord:
        if not isinstance(value, dict):
            raise ValueError("event record must be an object")
        expected = {
            "cycle_id",
            "cycle_identity_sha256",
            "event_id",
            "kind",
            "payload_json",
            "intent_hash",
            "from_phase",
            "to_phase",
        }
        if set(value) != expected:
            raise ValueError(f"event record fields must be exactly {sorted(expected)}")
        event = ProtocolEvent(
            cycle_id=value["cycle_id"],
            cycle_identity_sha256=value["cycle_identity_sha256"],
            event_id=value["event_id"],
            kind=EventKind(value["kind"]),
            payload_json=value["payload_json"],
            intent_hash=value["intent_hash"],
        )
        return cls(
            cycle_id=event.cycle_id,
            cycle_identity_sha256=event.cycle_identity_sha256,
            event_id=event.event_id,
            kind=event.kind,
            payload_json=event.payload_json,
            intent_hash=event.intent_hash,
            from_phase=Phase(value["from_phase"]),
            to_phase=Phase(value["to_phase"]),
        )

    @property
    def event(self) -> ProtocolEvent:
        return ProtocolEvent(
            cycle_id=self.cycle_id,
            cycle_identity_sha256=self.cycle_identity_sha256,
            event_id=self.event_id,
            kind=self.kind,
            payload_json=self.payload_json,
            intent_hash=self.intent_hash,
        )


@dataclass(frozen=True)
class EffectIntent:
    effect_id: str
    kind: EffectKind
    effect_class: EffectClass
    causation_event_id: str
    causation_intent_hash: str
    payload_json: str

    @property
    def payload(self) -> dict[str, Any]:
        return _json_object(self.payload_json, "effect payload_json")

    @classmethod
    def create(
        cls,
        event: ProtocolEvent,
        kind: EffectKind,
        effect_class: EffectClass,
        payload: dict[str, Any],
        *,
        ordinal: int,
    ) -> EffectIntent:
        payload_json = canonical_json_bytes(payload).decode("utf-8")
        effect_id = digest_json(
            {
                "event_intent_hash": event.intent_hash,
                "kind": kind.value,
                "ordinal": ordinal,
                "payload": json.loads(payload_json),
            },
            scope="ouroboros-effect-intent",
            schema_version=PROTOCOL_VERSION,
        ).value
        return cls(
            effect_id,
            kind,
            effect_class,
            event.event_id,
            event.intent_hash,
            payload_json,
        )


@dataclass(frozen=True)
class RunEvidence:
    role: RunRole
    run_id: str
    artifact_namespace: str
    outcome: RunOutcome
    observation: ObservationVerdict
    monitor: MonitorVerdict
    evidence_tier: EvidenceTier
    artifact: Digest
    material_lock_sha256: str
    executed_source: Digest

    def __post_init__(self) -> None:
        _require_text(self.run_id, "run_id")
        _require_text(self.artifact_namespace, "artifact_namespace")
        if _SHA256_RE.fullmatch(self.material_lock_sha256) is None:
            raise ValueError("material_lock_sha256 must be lowercase 64-hex")

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "run_id": self.run_id,
            "artifact_namespace": self.artifact_namespace,
            "outcome": self.outcome.value,
            "observation": self.observation.value,
            "monitor": self.monitor.value,
            "evidence_tier": self.evidence_tier.value,
            "artifact": self.artifact.to_dict(),
            "material_lock_sha256": self.material_lock_sha256,
            "executed_source": self.executed_source.to_dict(),
        }


@dataclass(frozen=True)
class FindingDisposition:
    finding_id: str
    disposition: Disposition
    bound_material_changed: bool
    change_evidence: Digest | None = None

    def __post_init__(self) -> None:
        _require_text(self.finding_id, "finding_id")
        if self.bound_material_changed:
            if self.disposition is not Disposition.FIXED or self.change_evidence is None:
                raise ValueError("a bound-material fix requires typed change evidence")
        elif self.change_evidence is not None:
            raise ValueError("change evidence is only valid for a bound-material change")

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "disposition": self.disposition.value,
            "bound_material_changed": self.bound_material_changed,
            "change_evidence": (
                self.change_evidence.to_dict() if self.change_evidence is not None else None
            ),
        }


@dataclass(frozen=True)
class CycleSnapshot:
    identity: CycleIdentity
    budget: ProtocolBudget
    phase: Phase = Phase.INIT
    revision: int = 0
    steps_used: int = 0
    material_lock: MaterialLock | None = None
    oracle: OracleBoundary | None = None
    events: tuple[EventRecord, ...] = field(default_factory=tuple)
    effects: tuple[EffectIntent, ...] = field(default_factory=tuple)
    runs: tuple[RunEvidence, ...] = field(default_factory=tuple)
    quarantine: Digest | None = None
    quarantine_namespace: str | None = None
    mutation_delta: Digest | None = None
    mutated_source: Digest | None = None
    mutation_active: bool = False
    restored_source: Digest | None = None
    finding_ids: tuple[str, ...] = field(default_factory=tuple)
    dispositions: tuple[FindingDisposition, ...] = field(default_factory=tuple)
    bound_material_changed: bool = False
    successor_cycle_id: str | None = None
    pending_interrupt: InterruptReason | None = None
    halt_reason: InterruptReason | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise ValueError("revision must be a non-negative integer")
        if self.revision > self.budget.max_steps + 2:
            raise ValueError("revision exceeds the ordinary-progress plus safety-tail bound")
        if isinstance(self.steps_used, bool) or not 0 <= self.steps_used <= self.budget.max_steps:
            raise ValueError("steps_used must be within the configured budget")
        if self.identity.generation >= self.budget.max_generations:
            raise ValueError("cycle generation must be below max_generations")
        if self.mutation_active and (self.mutation_delta is None or self.mutated_source is None):
            raise ValueError("mutation_active requires mutation and mutated-source identities")
        if self.phase in TERMINAL_PHASES and self.mutation_active:
            raise ValueError("a terminal snapshot cannot retain an active mutation")

    @classmethod
    def start(
        cls,
        cycle_id: str,
        budget: ProtocolBudget,
        *,
        generation: int = 0,
        previous_receipt_sha256: str | None = None,
    ) -> CycleSnapshot:
        return cls(
            identity=CycleIdentity(cycle_id, generation, previous_receipt_sha256),
            budget=budget,
        )


@dataclass(frozen=True)
class TransitionResult:
    accepted: bool
    replayed: bool
    snapshot: CycleSnapshot
    effects: tuple[EffectIntent, ...]
    rejection_code: str | None = None


__all__ = (
    "COMPLETION_EVIDENCE_TIERS",
    "PROTOCOL_VERSION",
    "RECEIPT_VERSION",
    "CycleIdentity",
    "CycleSnapshot",
    "Disposition",
    "EffectClass",
    "EffectIntent",
    "EffectKind",
    "EventKind",
    "EventRecord",
    "EvidenceTier",
    "FindingDisposition",
    "InterruptReason",
    "MaterialLock",
    "MonitorVerdict",
    "ObservationVerdict",
    "OracleBoundary",
    "Phase",
    "ProtocolBudget",
    "ProtocolEvent",
    "RunEvidence",
    "RunOutcome",
    "RunRole",
    "TERMINAL_PHASES",
    "TransitionResult",
)
