"""OpenLLMetry span → ooptdd gen_ai event bridge.

The verdict this pins (verified against the openllmetry clone, not assumed):
**zero-custom-emit does NOT work against the shipped `gen_ai` preset.** OpenLLMetry
names a tool with ``traceloop.span.kind="tool"`` + ``traceloop.entity.name=<name>``
(`opentelemetry-semantic-conventions-ai/.../__init__.py`: TRACELOOP_SPAN_KIND:117,
TRACELOOP_ENTITY_NAME:119, TraceloopSpanKindValues.TOOL:377), while it emits
``gen_ai.tool.call.arguments`` / ``gen_ai.tool.call.result`` for the payload
(langchain `callback_handler.py`:882, :910). It never emits ``gen_ai.tool.name`` —
the attribute the preset requires — so an instrumented app is gate-ready only
through an explicit mapping, which is what this bridge is.
"""
from __future__ import annotations

import pytest

from ooptdd.domain.ontology import check_conformance
from ooptdd.domain.semconv import gen_ai_ontology
from ooptdd.integrations.openllmetry import (
    TRACELOOP_ENTITY_NAME,
    TRACELOOP_SPAN_KIND,
    span_to_event,
    spans_to_events,
)


def _tool_span(**attrs):
    base = {TRACELOOP_SPAN_KIND: "tool", TRACELOOP_ENTITY_NAME: "search_web",
            "gen_ai.tool.call.arguments": '{"q": "x"}'}
    base.update(attrs)
    return {"name": "search_web.tool", "attributes": base}


def _llm_span(**attrs):
    base = {TRACELOOP_SPAN_KIND: "llm", "gen_ai.system": "anthropic",
            "gen_ai.request.model": "claude", "gen_ai.usage.input_tokens": 12}
    base.update(attrs)
    return {"name": "anthropic.chat", "attributes": base}


# ── the gap this bridge exists to close ────────────────────────────────────────
def test_raw_openllmetry_tool_span_fails_the_gen_ai_preset():
    # The honest baseline: feeding openllmetry's own attribute names straight into
    # the preset is RED — there is no gen_ai.tool.name in what it emits.
    raw = {"event": "gen_ai.execute_tool", **_tool_span()["attributes"]}
    assert check_conformance([raw], gen_ai_ontology())["passed"] is False


def test_bridged_tool_span_conforms():
    ev = span_to_event(_tool_span(), cid="c1")
    assert ev["event"] == "gen_ai.execute_tool"
    assert ev["gen_ai.tool.name"] == "search_web"
    assert ev["cid"] == "c1"
    assert check_conformance([ev], gen_ai_ontology())["passed"] is True


def test_bridged_llm_span_renames_system_to_provider():
    # gen_ai.system was renamed to gen_ai.provider.name at semconv 1.37; openllmetry
    # still emits the old key, so the bridge carries the rename.
    ev = span_to_event(_llm_span(), cid="c1")
    assert ev["event"] == "gen_ai.chat"
    assert ev["gen_ai.provider.name"] == "anthropic" and "gen_ai.system" not in ev
    assert check_conformance([ev], gen_ai_ontology())["passed"] is True


def test_provider_value_already_renamed_is_left_alone():
    ev = span_to_event(_llm_span(**{"gen_ai.system": None,
                                    "gen_ai.provider.name": "openai"}), cid="c")
    assert ev["gen_ai.provider.name"] == "openai"


def test_unmappable_span_kind_is_skipped_not_guessed():
    workflow = {"name": "wf", "attributes": {TRACELOOP_SPAN_KIND: "workflow",
                                             TRACELOOP_ENTITY_NAME: "pipeline"}}
    assert span_to_event(workflow, cid="c")["event"] == "gen_ai.invoke_workflow"
    unknown = {"name": "x", "attributes": {TRACELOOP_SPAN_KIND: "banana"}}
    assert span_to_event(unknown, cid="c") is None


def test_tool_span_without_a_name_is_not_silently_completed():
    # A tool span missing entity.name must NOT get a fabricated name — it stays
    # unnamed so the preset's required-attr check REDs, which is the truth.
    ev = span_to_event(_tool_span(**{TRACELOOP_ENTITY_NAME: None}), cid="c")
    assert "gen_ai.tool.name" not in ev
    assert check_conformance([ev], gen_ai_ontology())["passed"] is False


def test_spans_to_events_filters_and_preserves_order():
    spans = [_llm_span(), {"name": "x", "attributes": {TRACELOOP_SPAN_KIND: "banana"}},
             _tool_span()]
    evs = spans_to_events(spans, cid="c")
    assert [e["event"] for e in evs] == ["gen_ai.chat", "gen_ai.execute_tool"]
    assert [e["_emit_seq"] for e in evs] == [0, 2]  # original span positions kept


def test_agent_span_maps_with_entity_name_as_agent_id():
    span = {"name": "a", "attributes": {TRACELOOP_SPAN_KIND: "agent",
                                        TRACELOOP_ENTITY_NAME: "planner"}}
    ev = span_to_event(span, cid="c")
    assert ev["event"] == "gen_ai.invoke_agent" and ev["gen_ai.agent.id"] == "planner"
    assert check_conformance([ev], gen_ai_ontology())["passed"] is True


def test_bridge_does_not_invent_a_cid():
    with pytest.raises(ValueError):
        span_to_event(_tool_span(), cid="")
