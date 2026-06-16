"""Event ontology — give the gate semantic teeth.

A flat gate checks *names and counts*: "did event X arrive N times?". That cannot
see three whole classes of hallucination:

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
  hallucination class        JSON Schema construct         ooptdd field
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

It is **file-first** (zero KG, zero network — the offline invariant holds) and can
be mirrored into the KG when available; the KG never becomes a hard dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_NUMBER = (int, float)

# Transport/plumbing keys every envelope carries (see model.py). When an EventType
# is closed (`additional_properties: false`) these are never counted as "unexpected"
# attributes — closed-world polices the *payload* you declared, not the carrier.
ENVELOPE_KEYS = frozenset({
    "cid", "correlation_id", "cycle_id", "service", "level", "event",
    "_timestamp", "sig", "sig_alg", "sig_chain", "prev_sig",
    # W3C trace context (model.with_trace_context)
    "trace_id", "span_id",
    # CloudEvents context projection (model.cloudevents_envelope)
    "id", "source", "type", "specversion", "subject", "time", "datacontenttype",
})


@dataclass
class EventType:
    """One class in the ontology: an event name + what a valid instance must carry."""

    name: str
    required: list[str] = field(default_factory=list)   # attribute keys that must be present
    constraints: dict = field(default_factory=dict)     # attr -> {enum|type|min|max}
    description: str = ""
    #: JSON Schema ``additionalProperties: false`` — when False, a payload attribute
    #: that is neither declared (required/constraints) nor envelope plumbing is drift.
    additional_properties: bool = True

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
    }.get(t, True)  # unknown type name -> don't fail (forward-compatible)


@dataclass
class Ontology:
    types: dict[str, EventType] = field(default_factory=dict)
    #: when True, an observed event whose name is not a declared type is drift.
    closed_world: bool = False

    def get(self, name: str) -> EventType | None:
        return self.types.get(name)

    @classmethod
    def from_dict(cls, data: dict) -> Ontology:
        types = {}
        for name, spec in (data.get("event_types") or {}).items():
            spec = spec or {}
            types[name] = EventType(
                name=name,
                required=list(spec.get("required", [])),
                constraints=dict(spec.get("constraints", {})),
                description=spec.get("description", ""),
                additional_properties=bool(spec.get("additional_properties", True)),
            )
        return cls(types=types, closed_world=bool(data.get("closed_world", False)))

    @classmethod
    def from_file(cls, path: str) -> Ontology:
        import yaml

        with open(path) as fh:
            return cls.from_dict(yaml.safe_load(fh) or {})

    @classmethod
    def builtin(cls, name: str, **kwargs) -> Ontology:
        """Resolve a shipped preset ontology. Currently ``"gen_ai"`` — the version-pinned
        OpenTelemetry GenAI semconv vocabulary (see :mod:`ooptdd.semconv`)."""
        if name == "gen_ai":
            from .semconv import gen_ai_ontology
            return gen_ai_ontology(**kwargs)
        raise ValueError(f"unknown builtin ontology {name!r} (have: 'gen_ai')")


def check_conformance(
    events: list[dict],
    ontology: Ontology,
    *,
    event_type: str | None = None,
    closed_world: bool | None = None,
) -> dict:
    """Validate events against the ontology.

    ``event_type``: restrict to events of this name (None / "*" = all events).
    ``closed_world``: override the ontology default; when True an event whose name
    is not a declared type is reported as ``unknown_event_type`` drift.

    Returns ``{passed, checked, violations:[{event,index,problems}], unknown:[names]}``.
    """
    cw = ontology.closed_world if closed_world is None else closed_world
    scope_all = event_type in (None, "*")
    violations: list[dict] = []
    unknown: list[str] = []
    checked = 0
    for i, ev in enumerate(events):
        name = ev.get("event")
        if not scope_all and name != event_type:
            continue
        et = ontology.get(name)
        if et is None:
            # only flag unknowns we were asked to police: in closed-world, any in-scope
            # event must have a declared type.
            if cw and (scope_all or name == event_type):
                unknown.append(name)
                violations.append({"event": name, "index": i,
                                   "problems": ["unknown_event_type (closed-world drift)"]})
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
