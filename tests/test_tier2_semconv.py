"""Tier-2 #8: OTel GenAI semconv ontology preset + W3C trace-context correlation."""
from __future__ import annotations

from ooptdd.gate import evaluate
from ooptdd.model import with_trace_context
from ooptdd.ontology import Ontology, check_conformance
from ooptdd.semconv import GEN_AI_PROVIDERS, SEMCONV_VERSION, gen_ai_ontology


def test_builtin_resolves_gen_ai():
    ont = Ontology.builtin("gen_ai")
    assert ont.get("gen_ai.execute_tool") is not None
    assert "execute_tool" in [n.split(".")[-1] for n in ont.types]


def test_execute_tool_requires_tool_name():
    ont = gen_ai_ontology()
    # missing gen_ai.tool.name -> a flat count gate would pass; ontology makes it RED
    res = check_conformance(
        [{"event": "gen_ai.execute_tool"}], ont, event_type="gen_ai.execute_tool")
    assert not res["passed"]
    assert "gen_ai.tool.name" in res["violations"][0]["problems"][0]


def test_execute_tool_conforms_with_tool_name():
    ont = gen_ai_ontology()
    res = check_conformance(
        [{"event": "gen_ai.execute_tool", "gen_ai.tool.name": "search"}],
        ont, event_type="gen_ai.execute_tool")
    assert res["passed"]


def test_chat_provider_enum_includes_anthropic_and_rejects_typo():
    ont = gen_ai_ontology()
    good = check_conformance([{
        "event": "gen_ai.chat", "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": "claude-opus-4-8",
    }], ont, event_type="gen_ai.chat")
    bad = check_conformance([{
        "event": "gen_ai.chat", "gen_ai.provider.name": "anthropics",  # typo
        "gen_ai.request.model": "claude-opus-4-8",
    }], ont, event_type="gen_ai.chat")
    assert good["passed"] and not bad["passed"]
    assert "anthropic" in GEN_AI_PROVIDERS


def test_negative_token_usage_is_drift():
    ont = gen_ai_ontology()
    res = check_conformance([{
        "event": "gen_ai.chat", "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": "m", "gen_ai.usage.input_tokens": -3,
    }], ont, event_type="gen_ai.chat")
    assert not res["passed"]


def test_version_is_pinned():
    assert SEMCONV_VERSION and "experimental" in SEMCONV_VERSION


def test_gate_conforms_against_gen_ai_preset():
    # a gate can load the preset ontology and assert conformance over observed events.
    from ooptdd.backends.memory import MemoryBackend, reset
    reset()
    b = MemoryBackend()
    b.ship([{"cid": "c1", "event": "gen_ai.execute_tool", "gen_ai.tool.name": "grep"}])
    res = evaluate(b, {"cid": "c1", "expect": [{"conforms": "gen_ai.execute_tool"}]},
                   ontology=Ontology.builtin("gen_ai"))
    assert res["ok"]
    reset()


def test_with_trace_context_attaches_ids_nondestructively():
    rec = {"event": "x", "cid": "c1"}
    out = with_trace_context(rec, "abc123", "span9")
    assert out["trace_id"] == "abc123" and out["span_id"] == "span9"
    assert "trace_id" not in rec  # original untouched


def test_trace_ids_not_flagged_by_closed_world():
    from ooptdd.ontology import EventType
    et = EventType(name="x", required=[], additional_properties=False)
    assert et.validate({"event": "x", "trace_id": "t", "span_id": "s"}) == []
