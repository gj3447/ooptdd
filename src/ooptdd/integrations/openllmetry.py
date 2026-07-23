"""OpenLLMetry (Traceloop) spans → ooptdd ``gen_ai.*`` events.

**The finding this module encodes** (verified 2026-07-23 by reading the
openllmetry source, not inferred): an app instrumented by OpenLLMetry is *not*
gate-ready against the shipped ``gen_ai`` preset with zero custom emit. It names
a tool with ``traceloop.span.kind="tool"`` + ``traceloop.entity.name=<name>``
(`opentelemetry-semantic-conventions-ai/opentelemetry/semconv_ai/__init__.py`:
``TRACELOOP_SPAN_KIND``:117, ``TRACELOOP_ENTITY_NAME``:119,
``TraceloopSpanKindValues.TOOL``:377) and carries the payload in
``gen_ai.tool.call.arguments`` / ``gen_ai.tool.call.result``
(`opentelemetry-instrumentation-langchain/.../callback_handler.py`:882, :910).
It never emits ``gen_ai.tool.name`` — the attribute the preset requires. So the
honest answer to "can ooptdd gate an OpenLLMetry app for free?" is **no, but the
distance is one explicit mapping**, and this is that mapping.

Scope discipline: this is a pure attribute translation over span *dicts* — no
OTel SDK import, no collector, no live pipeline. It converts what an exporter
already handed you (`{"name":…, "attributes":{…}}`) into ooptdd event envelopes
you ship to a queryable backend. It never fabricates a missing required
attribute: an unnamed tool span stays unnamed so the ontology check goes RED,
which is the truth about that trace.
"""
from __future__ import annotations

TRACELOOP_SPAN_KIND = "traceloop.span.kind"
TRACELOOP_ENTITY_NAME = "traceloop.entity.name"

#: traceloop span kind -> the gen_ai operation ooptdd asserts on. Kinds outside this
#: map are skipped (returning None) rather than guessed into a wrong operation.
_KIND_TO_EVENT = {
    "tool": "gen_ai.execute_tool",
    "llm": "gen_ai.chat",
    "agent": "gen_ai.invoke_agent",
    "workflow": "gen_ai.invoke_workflow",
    "task": "gen_ai.invoke_workflow",
}

#: Where the entity name belongs, per operation (tool name vs agent id).
_NAME_ATTR = {
    "gen_ai.execute_tool": "gen_ai.tool.name",
    "gen_ai.invoke_agent": "gen_ai.agent.id",
}


def span_to_event(span: dict, *, cid: str) -> dict | None:
    """Translate ONE OpenLLMetry span dict into an ooptdd event envelope.

    Returns ``None`` for a span whose ``traceloop.span.kind`` has no honest gen_ai
    counterpart. Raises ``ValueError`` on an empty ``cid`` — a correlation id is the
    whole readback key, so silently inventing one would produce unqueryable events.
    """
    if not cid:
        raise ValueError("span_to_event needs a cid (the readback correlation key)")
    attrs = {k: v for k, v in (span.get("attributes") or {}).items() if v is not None}
    event_name = _KIND_TO_EVENT.get(attrs.get(TRACELOOP_SPAN_KIND))
    if event_name is None:
        return None
    out: dict = {"cid": cid, "event": event_name}
    entity = attrs.get(TRACELOOP_ENTITY_NAME)
    name_attr = _NAME_ATTR.get(event_name)
    if name_attr and entity:
        # NOT filled in when absent: a required attr the trace never carried must stay
        # missing so `conforms:` REDs on it.
        out[name_attr] = entity
    for key, value in attrs.items():
        if key in (TRACELOOP_SPAN_KIND, TRACELOOP_ENTITY_NAME):
            continue
        if key == "gen_ai.system":
            # renamed at semconv 1.37; openllmetry still emits the pre-rename key
            out.setdefault("gen_ai.provider.name", value)
            continue
        out.setdefault(key, value)
    return out


def spans_to_events(spans: list[dict], *, cid: str) -> list[dict]:
    """Translate a span list, dropping unmappable kinds. Each event keeps its
    ORIGINAL span index as ``_emit_seq`` — the emitter-authoritative ordering key
    ``must_order`` prefers over server page order (see ``engine/monitor.py``), so a
    ``trajectory:`` gate over bridged spans ranks by emission, not by store return."""
    out = []
    for idx, span in enumerate(spans):
        ev = span_to_event(span, cid=cid)
        if ev is not None:
            ev["_emit_seq"] = idx
            out.append(ev)
    return out
