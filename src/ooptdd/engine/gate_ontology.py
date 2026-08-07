"""Immutable ontology projection consumed by deterministic conformance monitors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..domain.ontology import ENVELOPE_KEYS, EventType, Ontology
from .gate_freeze import freeze_value, thaw_value


def _type_matches(actual: object, expected: object) -> bool:
    if not isinstance(expected, str):
        return True
    return {
        "number": isinstance(actual, int | float) and not isinstance(actual, bool),
        "int": isinstance(actual, int) and not isinstance(actual, bool),
        "float": isinstance(actual, float),
        "str": isinstance(actual, str),
        "bool": isinstance(actual, bool),
    }.get(expected, True)


def _constraint_problems(attribute: str, actual: object, rule: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    if "enum" in rule and actual not in rule["enum"]:
        problems.append(
            f"'{attribute}'={actual!r} not in enum {thaw_value(rule['enum'])}"
        )
    expected_type = rule.get("type")
    if expected_type is not None and not _type_matches(actual, expected_type):
        problems.append(f"'{attribute}'={actual!r} is not type {expected_type}")
    if "min" in rule and isinstance(actual, int | float) and actual < rule["min"]:
        problems.append(f"'{attribute}'={actual} < min {rule['min']}")
    if "max" in rule and isinstance(actual, int | float) and actual > rule["max"]:
        problems.append(f"'{attribute}'={actual} > max {rule['max']}")
    return problems


@dataclass(frozen=True)
class EventTypeSnapshot:
    name: str
    required: tuple[str, ...]
    constraints: Mapping[str, Any]
    additional_properties: bool

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise TypeError("event type snapshot name must be non-empty text")
        if not all(isinstance(item, str) for item in self.required):
            raise TypeError("event type snapshot required fields must be text")
        if not isinstance(self.constraints, Mapping):
            raise TypeError("event type snapshot constraints must be a mapping")
        if not all(
            isinstance(attribute, str) and isinstance(rule, Mapping)
            for attribute, rule in self.constraints.items()
        ):
            raise TypeError("event type snapshot constraints must map text to mappings")
        if not isinstance(self.additional_properties, bool):
            raise TypeError("event type snapshot additional_properties must be a bool")
        object.__setattr__(self, "required", tuple(self.required))
        object.__setattr__(self, "constraints", freeze_value(self.constraints))

    def validate(self, event: Mapping[str, Any]) -> list[str]:
        problems = [
            f"missing required attr '{key}'"
            for key in self.required
            if key not in event or event[key] is None
        ]
        for attribute, rule in self.constraints.items():
            if attribute in event and event[attribute] is not None:
                problems.extend(_constraint_problems(attribute, event[attribute], rule))
        if not self.additional_properties:
            allowed = set(self.required) | set(self.constraints) | ENVELOPE_KEYS
            problems.extend(
                f"unexpected attr '{key}' (additionalProperties:false)"
                for key in event
                if key not in allowed
            )
        return problems


def _snapshot_event_type(name: object, event_type: object) -> EventTypeSnapshot:
    validator = getattr(event_type, "validate", None)
    if not isinstance(event_type, EventType) or getattr(
        validator, "__func__", None
    ) is not EventType.validate:
        raise TypeError(
            f"ontology event type {name!r} has custom validation semantics "
            "that immutable capture cannot preserve"
        )
    if not isinstance(name, str) or event_type.name != name:
        raise TypeError("ontology event types must have matching string names")
    required = event_type.required
    constraints = event_type.constraints
    additional = event_type.additional_properties
    if (
        not isinstance(required, list | tuple)
        or not all(isinstance(item, str) for item in required)
        or not isinstance(constraints, Mapping)
        or not isinstance(additional, bool)
    ):
        raise TypeError(f"invalid ontology event type {name!r}")
    return EventTypeSnapshot(name, tuple(required), freeze_value(constraints), additional)


@dataclass(frozen=True)
class OntologySnapshot:
    types: Mapping[str, EventTypeSnapshot]
    closed_world: bool

    def __post_init__(self) -> None:
        if not isinstance(self.types, Mapping) or not all(
            isinstance(name, str)
            and isinstance(event_type, EventTypeSnapshot)
            and event_type.name == name
            for name, event_type in self.types.items()
        ):
            raise TypeError(
                "ontology snapshot types must map matching names to EventTypeSnapshot"
            )
        if not isinstance(self.closed_world, bool):
            raise TypeError("ontology snapshot closed_world must be a bool")
        object.__setattr__(self, "types", MappingProxyType(dict(self.types)))

    def get(self, name: str) -> EventTypeSnapshot | None:
        return self.types.get(name)

    @classmethod
    def capture(cls, value: object) -> OntologySnapshot:
        if isinstance(value, cls):
            return value
        getter = getattr(value, "get", None)
        if not isinstance(value, Ontology) or getattr(
            getter, "__func__", None
        ) is not Ontology.get:
            raise TypeError(
                "ontology has custom lookup semantics that immutable capture cannot preserve"
            )
        types = value.types
        closed_world = value.closed_world
        if not isinstance(types, Mapping) or not isinstance(closed_world, bool):
            raise TypeError("ontology has invalid canonical fields")
        snapshots = {
            name: _snapshot_event_type(name, event_type)
            for name, event_type in types.items()
        }
        return cls(MappingProxyType(snapshots), closed_world)


__all__ = ("EventTypeSnapshot", "OntologySnapshot")
