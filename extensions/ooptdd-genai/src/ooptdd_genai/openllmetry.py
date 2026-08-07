"""Pure, explicit translation from OpenLLMetry spans to ``gen_ai.*`` events.

This module imports no telemetry SDK and performs no I/O or registration. It maps
documented Traceloop span attributes into the version-pinned GenAI vocabulary and
leaves unavailable required attributes absent for downstream conformance checking.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from types import MappingProxyType

TRACELOOP_SPAN_KIND = "traceloop.span.kind"
TRACELOOP_ENTITY_NAME = "traceloop.entity.name"
_LEGACY_PROVIDER_NAME = "gen_ai.system"
_PROVIDER_NAME = "gen_ai.provider.name"

#: Traceloop span kind to standard GenAI event name. Unknown kinds are skipped.
_KIND_TO_EVENT = MappingProxyType(
    {
        "tool": "gen_ai.execute_tool",
        "llm": "gen_ai.chat",
        "agent": "gen_ai.invoke_agent",
        "workflow": "gen_ai.invoke_workflow",
        "task": "gen_ai.invoke_workflow",
    }
)

#: Where the entity name belongs, per operation (tool name vs agent id).
_NAME_ATTR = MappingProxyType(
    {
        "gen_ai.execute_tool": "gen_ai.tool.name",
        "gen_ai.invoke_agent": "gen_ai.agent.id",
    }
)


def span_to_event(span: Mapping[str, object], *, cid: str) -> dict | None:
    """Translate one OpenLLMetry span mapping into an event envelope.

    Unknown span kinds return ``None``. Malformed attributes and empty correlation
    identifiers are rejected instead of producing ambiguous events.
    """
    if not isinstance(cid, str) or not cid:
        raise ValueError("span_to_event needs a cid (the readback correlation key)")
    if not isinstance(span, Mapping):
        raise TypeError("span must be a mapping")
    raw_attrs = span.get("attributes", {})
    if not isinstance(raw_attrs, Mapping):
        raise TypeError("span attributes must be a mapping")
    if any(not isinstance(key, str) for key in raw_attrs):
        raise TypeError("span attribute names must be strings")
    attrs = {key: deepcopy(value) for key, value in raw_attrs.items() if value is not None}
    kind = attrs.get(TRACELOOP_SPAN_KIND)
    event_name = _KIND_TO_EVENT.get(kind) if isinstance(kind, str) else None
    if event_name is None:
        return None
    out: dict = {"cid": cid, "event": event_name}
    entity = attrs.get(TRACELOOP_ENTITY_NAME)
    name_attr = _NAME_ATTR.get(event_name)
    if name_attr and isinstance(entity, str) and entity:
        out[name_attr] = entity
    for key, value in attrs.items():
        if key in (TRACELOOP_SPAN_KIND, TRACELOOP_ENTITY_NAME):
            continue
        if key == _LEGACY_PROVIDER_NAME:
            out.setdefault(_PROVIDER_NAME, value)
            continue
        out.setdefault(key, value)
    return out


def spans_to_events(spans: Sequence[Mapping[str, object]], *, cid: str) -> list[dict]:
    """Translate spans while preserving each mapped span's input position."""
    out = []
    for idx, span in enumerate(spans):
        ev = span_to_event(span, cid=cid)
        if ev is not None:
            ev["_emit_seq"] = idx
            out.append(ev)
    return out
