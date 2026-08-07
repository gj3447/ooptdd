"""Validated immutable values for the generic Ouroboros kernel."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .identity import MAX_INTEROPERABLE_INTEGER, canonical_json_bytes, digest_json

Payload = dict[str, Any]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _decode_payload(value: str) -> Payload:
    decoded: object = json.loads(value)
    if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
        raise ValueError("event payload_json must be a JSON object with text keys")
    return {key: item for key, item in decoded.items() if isinstance(key, str)}


def _text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{label} must be non-empty text without NUL")


def _digest(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")


def _integer(value: object, label: str, *, minimum: int = 0) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= MAX_INTEROPERABLE_INTEGER
    ):
        raise ValueError(f"{label} is outside the interoperable integer range")


def _text_tuple(values: object, label: str, *, nonempty: bool = False) -> None:
    if not isinstance(values, tuple) or (nonempty and not values):
        raise TypeError(f"{label}s must be supplied as a tuple")
    if len(values) != len(set(values)):
        raise ValueError(f"{label}s must be unique")
    for value in values:
        _text(value, label)


@dataclass(frozen=True)
class ProtocolBudget:
    max_steps: int
    max_generations: int

    def __post_init__(self) -> None:
        _integer(self.max_steps, "max_steps", minimum=1)
        _integer(self.max_generations, "max_generations", minimum=1)


@dataclass(frozen=True)
class RevisionIdentity:
    """VCS-agnostic, namespaced identity for the material being processed."""

    namespace: str
    value: str

    def __post_init__(self) -> None:
        _text(self.namespace, "revision namespace")
        _text(self.value, "revision value")

    def to_dict(self) -> dict[str, str]:
        return {"namespace": self.namespace, "value": self.value}

    @property
    def digest(self) -> str:
        return digest_json(
            self.to_dict(), scope="ouroboros-revision", schema_version="revision/v1"
        ).value


@dataclass(frozen=True)
class TransitionRule:
    source: str
    event: str
    target: str
    validator: str | None = None

    def __post_init__(self) -> None:
        _text(self.source, "transition source")
        _text(self.event, "transition event")
        _text(self.target, "transition target")
        if self.validator is not None:
            _text(self.validator, "transition validator")


@dataclass(frozen=True)
class PayloadValidator:
    """Digest-bound declaration; executable behavior lives in PolicyEvaluator."""

    name: str
    version: str

    def __post_init__(self) -> None:
        _text(self.name, "validator name")
        _text(self.version, "validator version")


@dataclass(frozen=True)
class CompletionPolicy:
    version: str
    terminal_states: tuple[str, ...]
    required_authorities: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.version, "completion policy version")
        _text_tuple(self.terminal_states, "terminal state", nonempty=True)
        _text_tuple(self.required_authorities, "required authority")
        _text_tuple(self.required_artifacts, "required artifact")


@dataclass(frozen=True)
class CompletionEvidence:
    satisfied: bool
    authorities: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.satisfied, bool):
            raise TypeError("completion satisfied must be boolean")
        _text_tuple(self.authorities, "completion authority")
        _text_tuple(self.artifacts, "completion artifact")


@dataclass(frozen=True)
class RecoveryPolicy:
    version: str
    allowed: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _text(self.version, "recovery policy version")
        if not isinstance(self.allowed, tuple) or len(self.allowed) != len(set(self.allowed)):
            raise ValueError("recovery transitions must be a unique tuple")
        for item in self.allowed:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("each recovery transition must be a state/event tuple")
            _text(item[0], "recovery state")
            _text(item[1], "recovery event")


@dataclass(frozen=True)
class ProtocolDefinition:
    name: str
    version: str
    evaluator_version: str
    states: tuple[str, ...]
    events: tuple[str, ...]
    initial_state: str
    transitions: tuple[TransitionRule, ...]
    completion: CompletionPolicy
    recovery: RecoveryPolicy
    validators: tuple[PayloadValidator, ...] = ()

    def __post_init__(self) -> None:
        for value, label in (
            (self.name, "protocol name"),
            (self.version, "protocol version"),
            (self.evaluator_version, "evaluator version"),
        ):
            _text(value, label)
        _text_tuple(self.states, "state", nonempty=True)
        _text_tuple(self.events, "event", nonempty=True)
        if not isinstance(self.transitions, tuple) or not all(
            isinstance(item, TransitionRule) for item in self.transitions
        ):
            raise TypeError("transitions must be a tuple of TransitionRule")
        if not isinstance(self.validators, tuple) or not all(
            isinstance(item, PayloadValidator) for item in self.validators
        ):
            raise TypeError("validators must be a tuple of PayloadValidator")
        if not isinstance(self.completion, CompletionPolicy) or not isinstance(
            self.recovery, RecoveryPolicy
        ):
            raise TypeError("completion and recovery policies must be typed")
        self._validate_references()

    def _validate_references(self) -> None:
        if self.initial_state not in self.states:
            raise ValueError("initial_state must be declared")
        if not set(self.completion.terminal_states) <= set(self.states):
            raise ValueError("completion states must be declared")
        if self.initial_state in self.completion.terminal_states and (
            self.completion.required_authorities or self.completion.required_artifacts
        ):
            raise ValueError(
                "an initial terminal state cannot declare completion evidence requirements"
            )
        names = {item.name for item in self.validators}
        if len(names) != len(self.validators):
            raise ValueError("validator names must be unique")
        keys: set[tuple[str, str]] = set()
        for rule in self.transitions:
            if rule.source not in self.states or rule.target not in self.states:
                raise ValueError("transition states must be declared")
            if rule.event not in self.events or (rule.validator and rule.validator not in names):
                raise ValueError("transition event and validator must be declared")
            if (rule.source, rule.event) in keys:
                raise ValueError("each state/event pair must have one transition")
            keys.add((rule.source, rule.event))
        if any(
            state not in self.states or event not in self.events
            for state, event in self.recovery.allowed
        ):
            raise ValueError("recovery state and event must be declared")

    @property
    def digest(self) -> str:
        return digest_json(
            self.to_dict(), scope="ouroboros-policy", schema_version=self.version
        ).value

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "evaluator_version": self.evaluator_version,
            "states": list(self.states),
            "events": list(self.events),
            "initial_state": self.initial_state,
            "transitions": [vars(item) for item in self.transitions],
            "completion": {
                "version": self.completion.version,
                "terminal_states": list(self.completion.terminal_states),
                "required_authorities": list(self.completion.required_authorities),
                "required_artifacts": list(self.completion.required_artifacts),
            },
            "recovery": {
                "version": self.recovery.version,
                "allowed": [list(item) for item in self.recovery.allowed],
            },
            "validators": [vars(item) for item in self.validators],
        }


@dataclass(frozen=True)
class ProtocolEvent:
    event_id: str
    kind: str
    payload_json: str
    policy_digest: str

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        _text(self.kind, "event kind")
        _digest(self.policy_digest, "event policy_digest")
        try:
            payload = _decode_payload(self.payload_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("event payload_json must be valid JSON") from error
        if (
            not isinstance(payload, dict)
            or canonical_json_bytes(payload).decode() != self.payload_json
        ):
            raise ValueError("event payload_json must be a canonical object")

    @classmethod
    def create(
        cls, event_id: str, kind: str, payload: Payload, definition: ProtocolDefinition
    ) -> ProtocolEvent:
        return cls(event_id, kind, canonical_json_bytes(payload).decode(), definition.digest)

    @property
    def payload(self) -> Payload:
        return _decode_payload(self.payload_json)

    @property
    def intent_digest(self) -> str:
        return digest_json(
            {
                "event_id": self.event_id,
                "kind": self.kind,
                "payload": self.payload,
                "policy_digest": self.policy_digest,
            },
            scope="ouroboros-event-intent",
            schema_version="ouroboros-kernel/v1",
        ).value


@dataclass(frozen=True)
class TransitionRecord:
    event_id: str
    kind: str
    source: str
    target: str
    intent_digest: str
    policy_digest: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.event_id, "event_id"),
            (self.kind, "event kind"),
            (self.source, "source"),
            (self.target, "target"),
        ):
            _text(value, label)
        _digest(self.intent_digest, "intent_digest")
        _digest(self.policy_digest, "policy_digest")


@dataclass(frozen=True)
class ProtocolSnapshot:
    workflow_id: str
    material_revision: RevisionIdentity
    generation: int
    revision: int
    state: str
    policy_digest: str
    budget: ProtocolBudget
    steps_used: int = 0
    history: tuple[TransitionRecord, ...] = ()

    def __post_init__(self) -> None:
        _text(self.workflow_id, "workflow_id")
        _text(self.state, "state")
        _digest(self.policy_digest, "snapshot policy_digest")
        if not isinstance(self.material_revision, RevisionIdentity) or not isinstance(
            self.budget, ProtocolBudget
        ):
            raise TypeError("snapshot revision and budget must be typed")
        _integer(self.generation, "generation")
        _integer(self.revision, "revision")
        _integer(self.steps_used, "steps_used")
        if (
            self.generation >= self.budget.max_generations
            or self.steps_used > self.budget.max_steps
        ):
            raise ValueError("snapshot exceeds its configured budget")
        if not isinstance(self.history, tuple) or not all(
            isinstance(item, TransitionRecord) for item in self.history
        ):
            raise TypeError("history must be a tuple of TransitionRecord")
        if len({item.event_id for item in self.history}) != len(self.history):
            raise ValueError("history event IDs must be unique")
        if self.revision != len(self.history) or self.steps_used != len(self.history):
            raise ValueError("revision and steps_used must match accepted history")
        if self.history:
            if self.history[-1].target != self.state or any(
                left.target != right.source
                for left, right in zip(self.history, self.history[1:], strict=False)
            ):
                raise ValueError("history must be a contiguous path ending at snapshot state")
            if any(item.policy_digest != self.policy_digest for item in self.history):
                raise ValueError("history must bind the snapshot policy")

    @classmethod
    def initial(
        cls,
        workflow_id: str,
        material_revision: RevisionIdentity,
        definition: ProtocolDefinition,
        budget: ProtocolBudget,
        *,
        generation: int = 0,
    ) -> ProtocolSnapshot:
        return cls(
            workflow_id,
            material_revision,
            generation,
            0,
            definition.initial_state,
            definition.digest,
            budget,
        )


@dataclass(frozen=True)
class TransitionResult:
    accepted: bool
    replayed: bool
    snapshot: ProtocolSnapshot
    rejection_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool) or not isinstance(self.replayed, bool):
            raise TypeError("transition flags must be booleans")
        if not isinstance(self.snapshot, ProtocolSnapshot):
            raise TypeError("transition snapshot must be typed")
        if self.replayed and not self.accepted:
            raise ValueError("only accepted transitions can be replayed")
        if self.accepted == (self.rejection_code is not None):
            raise ValueError("accepted result and rejection_code are inconsistent")


__all__ = (
    "CompletionEvidence",
    "CompletionPolicy",
    "Payload",
    "PayloadValidator",
    "ProtocolBudget",
    "ProtocolDefinition",
    "ProtocolEvent",
    "ProtocolSnapshot",
    "RecoveryPolicy",
    "RevisionIdentity",
    "TransitionRecord",
    "TransitionResult",
    "TransitionRule",
)
