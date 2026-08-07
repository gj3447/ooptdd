"""Small, domain-neutral event vocabulary and conformance validation.

A flat gate checks *names and counts*: "did event X arrive N times?". That cannot
detect three whole classes of schema violation:

  1. **missing required attribute** — the code emits ``payment_authorized`` but with
     no ``amount``. Counted by name -> GREEN. Wrong.
  2. **unknown / fabricated event type** (drift) — the code emits an event whose
     type was never declared. A flat gate only asserts what you listed, so an
     undeclared name is invisible.
  3. **bad value** — ``status: "kinda"`` where the type allows only ``{ok, ng}``,
     or ``amount: "lots"`` where a number is required.

The ontology is a small, formal vocabulary of event types with required
attributes and value constraints. ``check_conformance`` validates observed events
against it, so the above become RED. It is deliberately minimal (required attrs +
enum/type/range) — an ontology earns its keep only when its types carry real
invariants; do not formalize for its own sake.

The semantics are borrowed verbatim from **JSON Schema** (Draft 2020-12), which is
the standard for exactly this job, so the three drift classes map 1:1 and stay
defensible:

  =========================  ============================  ====================
  violation class            JSON Schema construct         ooptdd field
  =========================  ============================  ====================
  missing required attr      ``"required": [...]``         ``required``
  bad value (enum/type)      ``"enum"`` / ``"type"``       ``constraints``
  unexpected attribute       ``"additionalProperties":     ``additional_properties:
                             false``                       false``
  unknown event type         (closed-world at the          ``Ontology.closed_world``
                             document level)
  =========================  ============================  ====================

We re-implement the small subset natively (no ``jsonschema`` dependency) to keep
the core stdlib-only and the offline invariant intact — but a spec author can read
the table above and reason about an EventType as the JSON Schema it denotes.

Definitions are validated eagerly.  A malformed or unsupported declaration is a
configuration error, never a constraint that silently becomes permissive.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

_NUMBER = (int, float)
_ROOT_FIELDS = frozenset({"event_types", "closed_world"})
_EVENT_TYPE_FIELDS = frozenset({"required", "constraints", "description", "additional_properties"})
_CONSTRAINT_FIELDS = frozenset({"enum", "type", "min", "max"})
_VALUE_TYPES = frozenset({"number", "int", "float", "str", "bool"})

# Transport/plumbing keys every envelope carries (see model.py). When an EventType
# is closed (`additional_properties: false`) these are never counted as "unexpected"
# attributes — closed-world polices the *payload* you declared, not the carrier.
ENVELOPE_KEYS = frozenset(
    {
        "cid",
        "correlation_id",
        "spec_version",
        "service",
        "level",
        "event",
        "_timestamp",
        "sig",
        "sig_alg",
        "sig_chain",
        "prev_sig",
        # W3C trace context (model.with_trace_context)
        "trace_id",
        "span_id",
        # CloudEvents context projection (model.cloudevents_envelope)
        "id",
        "source",
        "type",
        "specversion",
        "subject",
        "time",
        "datacontenttype",
    }
)


@dataclass(frozen=True)
class EventType:
    """One class in the ontology: an event name + what a valid instance must carry."""

    name: str
    required: tuple[str, ...] = field(default_factory=tuple)  # attribute keys that must be present
    constraints: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )  # attr -> {enum|type|min|max}
    description: str = ""
    #: JSON Schema ``additionalProperties: false`` — when False, a payload attribute
    #: that is neither declared (required/constraints) nor envelope plumbing is drift.
    additional_properties: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise TypeError("event type name must be non-empty text")
        if not isinstance(self.required, list | tuple) or not all(
            isinstance(key, str) and key for key in self.required
        ):
            raise TypeError(f"event type {self.name!r} required must contain non-empty text")
        if len(set(self.required)) != len(self.required):
            raise ValueError(f"event type {self.name!r} required fields must be unique")
        if not isinstance(self.constraints, Mapping):
            raise TypeError(f"event type {self.name!r} constraints must be a mapping")
        if not isinstance(self.description, str):
            raise TypeError(f"event type {self.name!r} description must be text")
        if not isinstance(self.additional_properties, bool):
            raise TypeError(f"event type {self.name!r} additional_properties must be a bool")
        object.__setattr__(self, "required", tuple(self.required))
        validated = {
            attribute: MappingProxyType(rule)
            for attribute, rule in _validated_constraints(self.name, self.constraints).items()
        }
        object.__setattr__(self, "constraints", MappingProxyType(validated))

    def _declared(self) -> set[str]:
        return set(self.required) | set(self.constraints)

    def validate(self, event: dict) -> list[str]:
        """Return a list of human-readable violation strings (empty = conforms)."""
        out: list[str] = []
        for key in self.required:
            if key not in event or event[key] is None:
                out.append(f"missing required attr '{key}'")
        for attr, rule in self.constraints.items():
            if attr not in event or event[attr] is None:
                continue  # presence is governed by `required`; constraints only bind when present
            val = event[attr]
            if "enum" in rule and val not in rule["enum"]:
                out.append(f"'{attr}'={val!r} not in enum {rule['enum']}")
            if "type" in rule and not _type_ok(val, rule["type"]):
                out.append(f"'{attr}'={val!r} is not type {rule['type']}")
            if "min" in rule and isinstance(val, _NUMBER) and val < rule["min"]:
                out.append(f"'{attr}'={val} < min {rule['min']}")
            if "max" in rule and isinstance(val, _NUMBER) and val > rule["max"]:
                out.append(f"'{attr}'={val} > max {rule['max']}")
        if not self.additional_properties:
            allowed = self._declared() | ENVELOPE_KEYS
            for key in event:
                if key not in allowed:
                    out.append(f"unexpected attr '{key}' (additionalProperties:false)")
        return out


def _type_ok(val, t: str) -> bool:
    return {
        "number": isinstance(val, _NUMBER) and not isinstance(val, bool),
        "int": isinstance(val, int) and not isinstance(val, bool),
        "float": isinstance(val, float),
        "str": isinstance(val, str),
        "bool": isinstance(val, bool),
    }[t]


def _validated_constraints(
    event_name: str, constraints: Mapping[str, object]
) -> dict[str, dict[str, Any]]:
    validated: dict[str, dict[str, Any]] = {}
    for attribute, raw_rule in constraints.items():
        if not isinstance(attribute, str) or not attribute:
            raise TypeError(f"event type {event_name!r} constraint names must be non-empty text")
        if not isinstance(raw_rule, Mapping):
            raise TypeError(f"event type {event_name!r} constraint {attribute!r} must be a mapping")
        unknown = set(raw_rule) - _CONSTRAINT_FIELDS
        if unknown:
            raise ValueError(
                f"event type {event_name!r} constraint {attribute!r} has unsupported fields: "
                f"{sorted(unknown)}"
            )
        rule = dict(raw_rule)
        expected_type = rule.get("type")
        if expected_type is not None and expected_type not in _VALUE_TYPES:
            raise ValueError(
                f"event type {event_name!r} constraint {attribute!r} has unsupported "
                f"type {expected_type!r}"
            )
        if "enum" in rule and not isinstance(rule["enum"], list | tuple):
            raise TypeError(
                f"event type {event_name!r} constraint {attribute!r} enum must be a sequence"
            )
        if "enum" in rule:
            rule["enum"] = tuple(rule["enum"])
        for bound in ("min", "max"):
            if bound in rule and (
                not isinstance(rule[bound], _NUMBER) or isinstance(rule[bound], bool)
            ):
                raise TypeError(
                    f"event type {event_name!r} constraint {attribute!r} {bound} must be numeric"
                )
        if "min" in rule and "max" in rule and rule["min"] > rule["max"]:
            raise ValueError(f"event type {event_name!r} constraint {attribute!r} min exceeds max")
        validated[attribute] = rule
    return validated


@dataclass(frozen=True)
class Ontology:
    types: Mapping[str, EventType] = field(default_factory=dict)
    #: when True, an observed event whose name is not a declared type is drift.
    closed_world: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.types, Mapping):
            raise TypeError("ontology types must be a mapping")
        if not isinstance(self.closed_world, bool):
            raise TypeError("ontology closed_world must be a bool")
        copied: dict[str, EventType] = {}
        for name, event_type in self.types.items():
            if not isinstance(name, str) or not name:
                raise TypeError("ontology event type names must be non-empty text")
            if not isinstance(event_type, EventType) or event_type.name != name:
                raise TypeError("ontology types must map names to matching EventType values")
            copied[name] = event_type
        object.__setattr__(self, "types", MappingProxyType(copied))

    def get(self, name: str) -> EventType | None:
        return self.types.get(name)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Ontology:
        if not isinstance(data, Mapping):
            raise TypeError("ontology definition must be a mapping")
        unknown_root = set(data) - _ROOT_FIELDS
        if unknown_root:
            raise ValueError(f"ontology has unsupported fields: {sorted(unknown_root)}")
        raw_types = data.get("event_types", {})
        if raw_types is None:
            raw_types = {}
        if not isinstance(raw_types, Mapping):
            raise TypeError("ontology event_types must be a mapping")
        raw_closed_world = data.get("closed_world", False)
        if not isinstance(raw_closed_world, bool):
            raise TypeError("ontology closed_world must be a bool")
        types: dict[str, EventType] = {}
        for name, raw_spec in raw_types.items():
            if not isinstance(name, str) or not name:
                raise TypeError("ontology event type names must be non-empty text")
            if raw_spec is None:
                raw_spec = {}
            if not isinstance(raw_spec, Mapping):
                raise TypeError(f"event type {name!r} definition must be a mapping")
            unknown_fields = set(raw_spec) - _EVENT_TYPE_FIELDS
            if unknown_fields:
                raise ValueError(
                    f"event type {name!r} has unsupported fields: {sorted(unknown_fields)}"
                )
            spec = dict(raw_spec)
            required = spec.get("required", ())
            constraints = spec.get("constraints", {})
            description = spec.get("description", "")
            additional_properties = spec.get("additional_properties", True)
            if not isinstance(required, list | tuple):
                raise TypeError(f"event type {name!r} required must be a sequence")
            if not isinstance(constraints, Mapping):
                raise TypeError(f"event type {name!r} constraints must be a mapping")
            if not isinstance(description, str):
                raise TypeError(f"event type {name!r} description must be text")
            if not isinstance(additional_properties, bool):
                raise TypeError(f"event type {name!r} additional_properties must be a bool")
            types[name] = EventType(
                name=name,
                required=tuple(required),
                constraints=constraints,
                description=description,
                additional_properties=additional_properties,
            )
        return cls(types=types, closed_world=raw_closed_world)

    @classmethod
    def from_file(cls, path: str) -> Ontology:
        import yaml

        # YAML is UTF-8 by specification (YAML 1.2 §5.2), so do not use the
        # platform locale codec.
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(yaml.safe_load(fh) or {})


_COMPAT_MODES = ("backward", "forward", "full")


def _enum(et: EventType, attr: str):
    rule = et.constraints.get(attr) or {}
    return set(rule["enum"]) if "enum" in rule else None


def ontology_compat(old: Ontology, new: Ontology, mode: str = "backward") -> dict:
    """Is the evolution ``old`` -> ``new`` compatible? (Confluent Schema Registry semantics.)

    ``backward`` — ``new`` can still validate data written under ``old`` (the common
    "upgrade consumers first" rule). Breaks: adding a required attr, shrinking an enum,
    and (when ``new`` is closed-world) dropping an event type that old data still emits.

    ``forward`` — ``old`` can validate data written under ``new``. Breaks: removing a
    required attr, growing an enum, and (when ``old`` is closed-world) adding an event type.

    ``full`` — both directions. Returns ``{compatible, mode, violations:[str]}``. This gates
    "did this EventType change *safely*?", a layer above per-instance validation.
    """
    if mode not in _COMPAT_MODES:
        raise ValueError(f"mode must be one of {_COMPAT_MODES}")
    back = mode in ("backward", "full")
    fwd = mode in ("forward", "full")
    v: list[str] = []
    names = set(old.types) | set(new.types)
    for name in sorted(names):
        o, n = old.get(name), new.get(name)
        if o is None:  # type added in new
            if fwd and old.closed_world:
                v.append(f"[forward] event type '{name}' added — old closed-world rejects it")
            continue
        if n is None:  # type removed in new
            if back and new.closed_world:
                v.append(f"[backward] event type '{name}' removed — new closed-world rejects it")
            continue
        o_req, n_req = set(o.required), set(n.required)
        if back:
            for a in n_req - o_req:
                v.append(f"[backward] '{name}': new required attr '{a}' — old data lacks it")
        if fwd:
            for a in o_req - n_req:
                v.append(f"[forward] '{name}': required attr '{a}' removed — old reader needs it")
        for attr in set(o.constraints) | set(n.constraints):
            oe, ne = _enum(o, attr), _enum(n, attr)
            if oe is not None and ne is not None:
                if back and (oe - ne):
                    v.append(f"[backward] '{name}.{attr}': enum shrank (dropped {sorted(oe - ne)})")
                if fwd and (ne - oe):
                    v.append(f"[forward] '{name}.{attr}': enum grew (added {sorted(ne - oe)})")
    return {"compatible": not v, "mode": mode, "violations": v}


def check_conformance(
    events: list[dict],
    ontology: Ontology,
    *,
    event_type: str | None = None,
    closed_world: bool | None = None,
    excluded_event_types: Collection[str] = (),
) -> dict:
    """Validate events against the ontology.

    ``event_type``: restrict to events of this name (None / "*" = all events).
    ``closed_world``: override the ontology default; when True an event whose name
    is not a declared type is reported as ``unknown_event_type`` drift.
    ``excluded_event_types``: exact carrier or control event names a caller explicitly
    excludes from this vocabulary. Prefixes and implicit framework exemptions are not used.

    Returns ``{passed, checked, violations:[{event,index,problems}], unknown:[names]}``.
    """
    cw = ontology.closed_world if closed_world is None else closed_world
    if not isinstance(cw, bool):
        raise TypeError("closed_world must be a bool")
    excluded = frozenset(excluded_event_types)
    if not all(isinstance(name, str) and name for name in excluded):
        raise TypeError("excluded_event_types must contain non-empty text")
    scope_all = event_type in (None, "*")
    violations: list[dict] = []
    unknown: list[str] = []
    checked = 0
    for i, ev in enumerate(events):
        name = ev.get("event")
        if not scope_all and name != event_type:
            continue
        if not isinstance(name, str):
            # An event envelope without a string event name cannot conform to any
            # declared vocabulary. Treat it as malformed evidence even in open-world
            # mode; silently skipping it could turn corrupt readback into a green gate.
            violations.append(
                {
                    "event": name,
                    "index": i,
                    "problems": ["event name must be a string"],
                }
            )
            continue
        if name in excluded:
            continue
        et = ontology.get(name)
        if et is None:
            # only flag unknowns we were asked to police: in closed-world, any in-scope
            # event must have a declared type.
            if cw and (scope_all or name == event_type):
                unknown.append(name)
                violations.append(
                    {
                        "event": name,
                        "index": i,
                        "problems": ["unknown_event_type (closed-world drift)"],
                    }
                )
            continue
        checked += 1
        problems = et.validate(ev)
        if problems:
            violations.append({"event": name, "index": i, "problems": problems})
    return {
        "passed": not violations,
        "checked": checked,
        "violations": violations,
        "unknown": unknown,
    }
