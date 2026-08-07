"""Immutable values crossing the gate shell/kernel boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .gate_freeze import freeze_value, thaw_value
from .gate_ontology import OntologySnapshot

JsonMap = dict[str, Any]


@dataclass(frozen=True)
class ExternalObservation:
    """An external fact captured by the effect shell before judgement begins."""

    reachable: bool
    value: object = None
    complete: bool = True
    separate_source: bool = False
    derived_identity: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("reachable", "complete", "separate_source"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"external observation {field_name} must be a bool")
        if self.derived_identity is not None and not isinstance(self.derived_identity, str):
            raise TypeError("external observation derived_identity must be text or None")
        object.__setattr__(self, "value", freeze_value(self.value))


@dataclass(frozen=True)
class ReplayProbe:
    """Compatibility view for legacy custom checks; it only replays a frozen fact."""

    observation: ExternalObservation

    def __post_init__(self) -> None:
        if not isinstance(self.observation, ExternalObservation):
            raise TypeError("replay probe requires an ExternalObservation")

    def probe(self, kind: str, selector: object, cid: str) -> ExternalObservation:
        del kind, selector, cid
        return self.observation


@dataclass(frozen=True)
class CheckCtx:
    """Explicit, read-only context passed to one check predicate."""

    reachable: bool
    indicators: Mapping[str, Any]
    ontology: object | None = None
    allow_errors: tuple[Mapping[str, Any], ...] = ()
    external_observation: ExternalObservation | None = None
    probe: object | None = None
    cid: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reachable, bool):
            raise TypeError("check context reachable must be a bool")
        if not isinstance(self.indicators, Mapping):
            raise TypeError("check context indicators must be a mapping")
        if self.external_observation is not None and not isinstance(
            self.external_observation, ExternalObservation
        ):
            raise TypeError("check context external_observation has an invalid type")
        if self.cid is not None and not isinstance(self.cid, str):
            raise TypeError("check context cid must be text or None")
        if not isinstance(self.allow_errors, tuple) or any(
            not isinstance(value, Mapping) for value in self.allow_errors
        ):
            raise TypeError("check context allow_errors must be a tuple of mappings")
        object.__setattr__(self, "indicators", freeze_value(self.indicators))
        object.__setattr__(
            self,
            "allow_errors",
            tuple(freeze_value(value) for value in self.allow_errors),
        )
        object.__setattr__(
            self,
            "ontology",
            None if self.ontology is None else OntologySnapshot.capture(self.ontology),
        )


CheckFn = Callable[[list[JsonMap], JsonMap, CheckCtx], JsonMap]


def _snapshot_registries(
    registry: Mapping[str, CheckFn],
    strength_by_key: Mapping[str, str],
) -> tuple[Mapping[str, CheckFn], Mapping[str, str]]:
    handlers = dict(registry)
    if not all(
        isinstance(key, str) and key and callable(handler)
        for key, handler in handlers.items()
    ):
        raise ValueError("gate evaluation handlers must map non-empty text to callables")
    strengths = dict(strength_by_key)
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in strengths.items()
    ):
        raise TypeError("gate strength registry must map strings to strings")
    return MappingProxyType(handlers), MappingProxyType(strengths)


def _snapshot_indexed_values(
    values: Mapping[int, object],
    *,
    label: str,
    expected_type: type | None = None,
) -> Mapping[int, Any]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{label} must be a mapping")
    captured: dict[int, Any] = {}
    for index, value in values.items():
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise TypeError(f"{label} indexes must be non-negative integers")
        if expected_type is not None and not isinstance(value, expected_type):
            raise TypeError(f"{label} have invalid value types")
        if expected_type is None and not isinstance(value, Mapping):
            raise TypeError(f"{label} must be mappings")
        captured[index] = value if expected_type is not None else freeze_value(value)
    return MappingProxyType(captured)


@dataclass(frozen=True)
class GatePolicy:
    """All policy that used to be read implicitly during event judgement."""

    forbid_errors: bool = False
    require_corroboration: bool = False
    require_signature: bool = False
    signing_key: str | None = None
    require_independent_store: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "forbid_errors",
            "require_corroboration",
            "require_signature",
            "require_independent_store",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"gate policy {field_name} must be a bool")
        if self.signing_key is not None and not isinstance(self.signing_key, str):
            raise TypeError("gate policy signing_key must be text or None")


@dataclass(frozen=True)
class GateSource:
    """Identity and honesty fields of one already-completed readback."""

    cid: str
    reachable: bool
    complete: bool = True
    emit_backend: str | None = None
    emit_identity: str | None = None
    emit_independent: bool | None = None
    emit_sampled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.cid, str) or not self.cid:
            raise ValueError("gate source cid must be a non-empty string")
        for field_name in ("reachable", "complete", "emit_sampled"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"gate source {field_name} must be a bool")
        for field_name in ("emit_backend", "emit_identity"):
            if getattr(self, field_name) is not None and not isinstance(
                getattr(self, field_name), str
            ):
                raise TypeError(f"gate source {field_name} must be text or None")
        if self.emit_independent is not None and not isinstance(self.emit_independent, bool):
            raise TypeError("gate source emit_independent must be a bool or None")


@dataclass(frozen=True)
class GateEvaluation:
    """Closed input value for one deterministic gate judgement."""

    spec: Mapping[str, Any]
    events: tuple[Mapping[str, Any], ...]
    policy: GatePolicy
    source: GateSource
    registry: Mapping[str, CheckFn]
    strength_by_key: Mapping[str, str]
    external_observations: Mapping[int, ExternalObservation]
    ontology: object | None = None
    captured_check_results: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_gate_evaluation_shape(self)
        handlers, strengths = _snapshot_registries(
            self.registry, self.strength_by_key
        )
        object.__setattr__(self, "spec", freeze_value(self.spec))
        object.__setattr__(self, "events", tuple(freeze_value(event) for event in self.events))
        object.__setattr__(self, "registry", handlers)
        object.__setattr__(self, "strength_by_key", strengths)
        object.__setattr__(
            self,
            "external_observations",
            _snapshot_indexed_values(
                self.external_observations,
                label="external observations",
                expected_type=ExternalObservation,
            ),
        )
        object.__setattr__(
            self,
            "captured_check_results",
            _snapshot_indexed_values(
                self.captured_check_results,
                label="captured check results",
            ),
        )
        object.__setattr__(
            self,
            "ontology",
            None if self.ontology is None else OntologySnapshot.capture(self.ontology),
        )

    @classmethod
    def capture(
        cls,
        spec: Mapping[str, Any],
        events: list[JsonMap],
        *,
        policy: GatePolicy,
        source: GateSource,
        registry: Mapping[str, CheckFn],
        strength_by_key: Mapping[str, str],
        external_observations: Mapping[int, ExternalObservation] | None = None,
        ontology: object | None = None,
    ) -> GateEvaluation:
        return cls(
            spec=spec,
            events=tuple(events),
            policy=policy,
            source=source,
            registry=registry,
            strength_by_key=strength_by_key,
            external_observations=external_observations or {},
            ontology=ontology,
        )


def _validate_gate_evaluation_shape(evaluation: GateEvaluation) -> None:
    if not isinstance(evaluation.policy, GatePolicy) or not isinstance(
        evaluation.source, GateSource
    ):
        raise TypeError("gate evaluation requires typed policy and source values")
    if not isinstance(evaluation.spec, Mapping):
        raise TypeError("gate evaluation spec must be a mapping")
    if not isinstance(evaluation.events, tuple) or any(
        not isinstance(event, Mapping) for event in evaluation.events
    ):
        raise TypeError("gate evaluation events must be a tuple of mappings")
    if not isinstance(evaluation.registry, Mapping) or not isinstance(
        evaluation.strength_by_key, Mapping
    ):
        raise TypeError("gate evaluation registries must be mappings")
    if not isinstance(evaluation.external_observations, Mapping) or not isinstance(
        evaluation.captured_check_results, Mapping
    ):
        raise TypeError("gate evaluation captured values must be mappings")


@dataclass(frozen=True)
class GateVerdict:
    """Deeply immutable verdict with an explicit compatibility conversion."""

    data: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.data, Mapping):
            raise TypeError("gate verdict root must be a mapping")
        object.__setattr__(self, "data", freeze_value(self.data))

    @classmethod
    def capture(cls, value: Mapping[str, Any]) -> GateVerdict:
        return cls(value)

    def as_dict(self) -> JsonMap:
        value = thaw_value(self.data)
        if not isinstance(value, dict):
            raise TypeError("gate verdict root must be an object")
        return value

    to_dict = as_dict


__all__ = (
    "CheckCtx",
    "CheckFn",
    "ExternalObservation",
    "GateEvaluation",
    "GatePolicy",
    "GateSource",
    "GateVerdict",
    "JsonMap",
    "ReplayProbe",
    "freeze_value",
    "thaw_value",
)
