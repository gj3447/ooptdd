"""Tests for GenAI event construction and semantic-convention conformance."""

from __future__ import annotations

import pytest
from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.domain.ontology import check_conformance
from ooptdd.engine.gate import evaluate

from ooptdd_genai.events import execute_tool_event, invoke_agent_event
from ooptdd_genai.semconv import gen_ai_ontology

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"  # W3C trace_id (16-byte hex)


def test_invoke_agent_unifies_cid_with_trace_id():
    ev = invoke_agent_event(trace_id=TRACE, agent_id="planner", agent_name="Planner")
    assert ev["trace_id"] == TRACE
    # All correlation aliases refer to the same trace identity.
    assert ev["cid"] == ev["correlation_id"] == ev["cycle_id"] == TRACE
    assert ev["event"] == "gen_ai.invoke_agent"


def test_invoke_agent_conforms_to_semconv():
    ev = invoke_agent_event(trace_id=TRACE, agent_id="agent-1")
    res = check_conformance([ev], gen_ai_ontology(), event_type="gen_ai.invoke_agent")
    assert res["passed"], res


def test_execute_tool_conforms_and_unifies():
    ev = execute_tool_event(trace_id=TRACE, tool_name="kg_query", tool_call_id="call-1")
    assert ev["cid"] == TRACE and ev["event"] == "gen_ai.execute_tool"
    res = check_conformance([ev], gen_ai_ontology(), event_type="gen_ai.execute_tool")
    assert res["passed"], res


def test_unknown_operation_rejected():
    from ooptdd_genai.events import _gen_ai_event

    with pytest.raises(ValueError):
        _gen_ai_event("not_an_op", TRACE, None, {})


def test_backend_round_trip_is_keyed_by_trace_id():
    reset()
    b = MemoryBackend()
    b.ship(
        [
            invoke_agent_event(trace_id=TRACE, agent_id="agent-1"),
            execute_tool_event(trace_id=TRACE, tool_name="search"),
        ]
    )
    res = evaluate(
        b,
        {
            "cid": TRACE,
            "expect": [
                {"conforms": "gen_ai.invoke_agent"},
                {"present": [{"event": "gen_ai.execute_tool"}]},
            ],
        },
        ontology=gen_ai_ontology(),
    )
    assert res["ok"], res
    reset()


def test_extra_attributes_are_deeply_snapshotted():
    payload = {"nested": {"items": [1]}}
    event = execute_tool_event(trace_id=TRACE, tool_name="search", payload=payload)

    payload["nested"]["items"].append(2)

    assert event["payload"] == {"nested": {"items": [1]}}
