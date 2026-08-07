"""Pure structural analysis for caller-defined protocol state machines."""

from __future__ import annotations

from dataclasses import dataclass

from .model import ProtocolDefinition


@dataclass(frozen=True)
class ConformanceFinding:
    """One deterministic structural mismatch with reducer semantics."""

    code: str
    state: str | None = None
    event: str | None = None
    target: str | None = None
    transition_index: int | None = None


@dataclass(frozen=True)
class InvalidEventSemantics:
    """Ordered invalid-event and replay behavior implemented by ``step``.

    Policy binding is checked before the history branch. For a fresh event ID,
    the first applicable code in ``fresh_rejection_precedence`` wins. The
    terminal outcome therefore requires remaining step budget.
    """

    policy_mismatch: str = "reject:policy_mismatch"
    exact_replay: str = "accept:unchanged_snapshot"
    conflicting_replay: str = "reject:event_identity_conflict"
    fresh_rejection_precedence: tuple[str, ...] = (
        "reject:step_budget_exhausted",
        "reject:unknown_state",
        "reject:terminal_state",
        "reject:unknown_event",
        "reject:unknown_transition",
        "reject:invalid_payload",
        "reject:recovery_policy_denied",
        "reject:completion_policy_failed",
    )
    exceptional_failure: str = "reject:evaluator_failure"


@dataclass(frozen=True)
class ProtocolConformance:
    reachable_states: tuple[str, ...]
    unreachable_states: tuple[str, ...]
    reachable_transition_indexes: tuple[int, ...]
    findings: tuple[ConformanceFinding, ...]
    invalid_event_semantics: InvalidEventSemantics

    @property
    def conformant(self) -> bool:
        return not self.findings


def _transition_classes(
    definition: ProtocolDefinition,
) -> tuple[frozenset[int], frozenset[int], tuple[int, ...]]:
    terminal_states = set(definition.completion.terminal_states)
    allowed_recovery = set(definition.recovery.allowed)
    recovery_states = {state for state, _ in definition.recovery.allowed}
    terminal_indexes = frozenset(
        index for index, rule in enumerate(definition.transitions) if rule.source in terminal_states
    )
    denied_recovery_indexes = frozenset(
        index
        for index, rule in enumerate(definition.transitions)
        if rule.source in recovery_states
        and rule.source not in terminal_states
        and (rule.source, rule.event) not in allowed_recovery
    )
    executable_indexes = tuple(
        index
        for index in range(len(definition.transitions))
        if index not in terminal_indexes and index not in denied_recovery_indexes
    )
    return terminal_indexes, denied_recovery_indexes, executable_indexes


def _reachable_states(
    definition: ProtocolDefinition, executable_indexes: tuple[int, ...]
) -> frozenset[str]:
    reachable = {definition.initial_state}
    changed = True
    while changed:
        changed = False
        for index in executable_indexes:
            rule = definition.transitions[index]
            if rule.source in reachable and rule.target not in reachable:
                reachable.add(rule.target)
                changed = True
    return frozenset(reachable)


def _terminal_findings(
    definition: ProtocolDefinition, terminal_indexes: frozenset[int]
) -> tuple[ConformanceFinding, ...]:
    return tuple(
        ConformanceFinding(
            "terminal_outgoing_transition",
            rule.source,
            rule.event,
            rule.target,
            index,
        )
        for index, rule in enumerate(definition.transitions)
        if index in terminal_indexes
    )


def _recovery_findings(
    definition: ProtocolDefinition, denied_recovery_indexes: frozenset[int]
) -> tuple[ConformanceFinding, ...]:
    declared_pairs = {(rule.source, rule.event) for rule in definition.transitions}
    missing = tuple(
        ConformanceFinding("recovery_pair_without_transition", state, event)
        for state, event in definition.recovery.allowed
        if (state, event) not in declared_pairs
    )
    denied = tuple(
        ConformanceFinding(
            "recovery_transition_not_allowlisted",
            rule.source,
            rule.event,
            rule.target,
            index,
        )
        for index, rule in enumerate(definition.transitions)
        if index in denied_recovery_indexes
    )
    return (*missing, *denied)


def _unreachable_findings(
    definition: ProtocolDefinition,
    executable_indexes: tuple[int, ...],
    reachable: frozenset[str],
) -> tuple[ConformanceFinding, ...]:
    states = tuple(
        ConformanceFinding("unreachable_state", state)
        for state in definition.states
        if state not in reachable
    )
    transitions = tuple(
        ConformanceFinding(
            "unreachable_transition",
            definition.transitions[index].source,
            definition.transitions[index].event,
            definition.transitions[index].target,
            index,
        )
        for index in executable_indexes
        if definition.transitions[index].source not in reachable
    )
    return (*states, *transitions)


def analyze_definition(definition: ProtocolDefinition) -> ProtocolConformance:
    """Analyze structural reachability using the reducer's actual admission rules.

    Payload validators remain opaque, so this analysis does not claim semantic
    reachability or liveness. Partial state/event functions and cycles are valid.
    """

    terminal, denied_recovery, executable = _transition_classes(definition)
    reachable = _reachable_states(definition, executable)
    findings = (
        *_terminal_findings(definition, terminal),
        *_recovery_findings(definition, denied_recovery),
        *_unreachable_findings(definition, executable, reachable),
    )

    reachable_indexes = tuple(
        index for index in executable if definition.transitions[index].source in reachable
    )
    return ProtocolConformance(
        tuple(state for state in definition.states if state in reachable),
        tuple(state for state in definition.states if state not in reachable),
        reachable_indexes,
        tuple(findings),
        InvalidEventSemantics(),
    )


__all__ = (
    "ConformanceFinding",
    "InvalidEventSemantics",
    "ProtocolConformance",
    "analyze_definition",
)
