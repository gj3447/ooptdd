"""A toy LLM-agent loop emitting OTel GenAI-semconv events (Tier-3 #12 dogfood).

This stands in for a real agent. Each step emits a structured event named by the
``gen_ai.*`` operation, carrying the standard attributes (``gen_ai.tool.name``,
``gen_ai.request.model``, ``gen_ai.provider.name``). ooptdd then asserts on those
emitted events — read back from a store — instead of trusting the agent's "done!".

### In production you would NOT hand-emit these

Drop in **OpenLLMetry** (``pip install traceloop-sdk``) to auto-instrument the LLM
and tool calls; it emits ``gen_ai.*`` telemetry over **OTLP**, which ooptdd's
``otel`` backend already speaks::

    from traceloop.sdk import Traceloop
    Traceloop.init(app_name="my-agent")          # auto-instruments Anthropic/tools -> OTLP
    backend = get_backend("otel", simple=True)   # ship path; read back via your store

The hand-written emitter below keeps the example runnable with zero dependencies
(memory backend), but the event *shape* is the same one OpenLLMetry produces — so
the gate in ``test_agent_gen_ai.py`` is identical for the auto-instrumented case.
"""
from __future__ import annotations

from ooptdd.backends import Backend


def run_agent(backend: Backend, cid: str, *, tools: list[str],
              model: str = "claude-opus-4-8") -> dict:
    """Run a one-turn agent: invoke -> chat -> execute each tool. Emits gen_ai.* events."""
    def ev(event, **attrs):
        return {"cid": cid, "correlation_id": cid, "cycle_id": cid,
                "service": "demo.agent", "event": event, **attrs}

    backend.ship([ev("gen_ai.invoke_agent", **{"gen_ai.agent.id": "agent-1"})])
    backend.ship([ev("gen_ai.chat", **{"gen_ai.provider.name": "anthropic",
                                       "gen_ai.request.model": model,
                                       "gen_ai.usage.input_tokens": 120,
                                       "gen_ai.usage.output_tokens": 64})])
    for t in tools:
        backend.ship([ev("gen_ai.execute_tool", **{"gen_ai.tool.name": t})])
    # As ever, the return value is not the evidence — the emitted trace is.
    return {"status": "ok", "tools_called": len(tools)}
