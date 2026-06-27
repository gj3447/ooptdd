"""gen_ai emit 헬퍼 TDD — cid≡trace_id 통일 + semconv 필수 attr 충족 + cross-tool 라운드트립.

# KG: gen-ai-emit-cid-trace-unify-2026-06-27
"""
from __future__ import annotations

import pytest

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.domain.ontology import Ontology, check_conformance
from ooptdd.domain.semconv import gen_ai_ontology
from ooptdd.engine.gate import evaluate
from ooptdd.gen_ai_emit import execute_tool_event, invoke_agent_event

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"  # W3C trace_id (16-byte hex)


def test_invoke_agent_unifies_cid_with_trace_id():
    ev = invoke_agent_event(trace_id=TRACE, agent_id="prometheus", agent_name="획득")
    assert ev["trace_id"] == TRACE
    # cid ≡ correlation_id ≡ cycle_id ≡ trace_id (한 트레이스 공간)
    assert ev["cid"] == ev["correlation_id"] == ev["cycle_id"] == TRACE
    assert ev["event"] == "gen_ai.invoke_agent"


def test_invoke_agent_conforms_to_semconv():
    ev = invoke_agent_event(trace_id=TRACE, agent_id="hades")
    res = check_conformance([ev], gen_ai_ontology(), event_type="gen_ai.invoke_agent")
    assert res["passed"], res


def test_execute_tool_conforms_and_unifies():
    ev = execute_tool_event(trace_id=TRACE, tool_name="kg_query", tool_call_id="call-1")
    assert ev["cid"] == TRACE and ev["event"] == "gen_ai.execute_tool"
    res = check_conformance([ev], gen_ai_ontology(), event_type="gen_ai.execute_tool")
    assert res["passed"], res


def test_unknown_operation_rejected():
    from ooptdd.gen_ai_emit import _gen_ai_event

    with pytest.raises(ValueError):
        _gen_ai_event("not_an_op", TRACE, None, {})


def test_cross_tool_positive_arrival_keyed_by_trace_id():
    # legion 이 trace_id 로 ship → verify 가 같은 trace_id(=cid)로 read → gate conforms.
    reset()
    b = MemoryBackend()
    b.ship([invoke_agent_event(trace_id=TRACE, agent_id="prometheus"),
            execute_tool_event(trace_id=TRACE, tool_name="grep")])
    res = evaluate(
        b,
        {"cid": TRACE, "expect": [
            {"conforms": "gen_ai.invoke_agent"},
            {"present": [{"event": "gen_ai.execute_tool"}]},
        ]},
        ontology=Ontology.builtin("gen_ai"),
    )
    assert res["ok"], res
    reset()
