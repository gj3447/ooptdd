"""Assert an agent's behaviour from its emitted GenAI telemetry (Tier-3 #12 + #8/#9/#10).

Run it:  pytest examples/test_agent_gen_ai.py -s

Zero infrastructure (in-memory backend). The same gate runs unchanged when the
events come from OpenLLMetry over OTLP instead of the hand-emitter — see
``agent_gen_ai.py``. Shows the borrowed surfaces working together:

  * ``conforms`` against the built-in **OTel GenAI** ontology (Tier-2 #8)
  * ``present`` subset + ``trajectory`` ordered sequence (Tier-1/2)
  * ``within_s`` bounded interval (Tier-3 #10)
  * ``assert_gate`` DeepEval-style in-test assertion (Tier-2 #9)
"""
from __future__ import annotations

from examples.agent_gen_ai import run_agent
from ooptdd import Ontology, assert_gate
from ooptdd.backends.memory import MemoryBackend, reset


def test_agent_trace_conforms_and_sequences():
    reset()
    b = MemoryBackend()
    cid = "agent-demo-1"
    run_agent(b, cid, tools=["search", "read_file"])

    spec = {
        "cid": cid,
        "expect": [
            # every gen_ai.execute_tool must carry gen_ai.tool.name (semconv ontology)
            {"conforms": "gen_ai.execute_tool"},
            {"conforms": "gen_ai.chat"},
            # the expected operations occurred (any order)
            {"present": [{"event": "gen_ai.invoke_agent"}, {"event": "gen_ai.chat"}]},
            # the agent invoked, then chatted, then ran a tool — in order, promptly
            {"trajectory": ["gen_ai.invoke_agent", "gen_ai.chat", "gen_ai.execute_tool"],
             "within_s": 60},
            # at least two tool calls
            {"event": "gen_ai.execute_tool", "op": "gte", "target": 2},
        ],
    }
    res = assert_gate(spec, backend=b, ontology=Ontology.builtin("gen_ai"))
    assert res["ok"]
    reset()
