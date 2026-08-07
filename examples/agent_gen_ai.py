"""A toy LLM-agent loop emitting OTel GenAI-semconv events (Tier-3 #12 dogfood).

This stands in for a real agent. Each step uses the explicitly installed
``ooptdd-genai`` builders to emit a structured ``gen_ai.*`` event. The base
framework then evaluates those events after reading them back from a store.

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

from ooptdd_genai import execute_tool_event, invoke_agent_event

from ooptdd.backends import Backend


def run_agent(
    backend: Backend,
    cid: str,
    *,
    tools: list[str],
    model: str = "claude-opus-4-8",
) -> dict:
    """Run a one-turn agent: invoke -> chat -> execute each tool. Emits gen_ai.* events."""
    backend.ship(
        [
            invoke_agent_event(
                trace_id=cid,
                agent_id="agent-1",
                provider="anthropic",
                request_model=model,
            ),
            {
                "cid": cid,
                "correlation_id": cid,
                "cycle_id": cid,
                "service": "demo.agent",
                "event": "gen_ai.chat",
                "gen_ai.provider.name": "anthropic",
                "gen_ai.request.model": model,
                "gen_ai.usage.input_tokens": 120,
                "gen_ai.usage.output_tokens": 64,
            },
        ]
    )
    for index, tool in enumerate(tools):
        backend.ship(
            [
                execute_tool_event(
                    trace_id=cid,
                    tool_name=tool,
                    tool_call_id=f"call-{index}",
                )
            ]
        )
    # As ever, the return value is not the evidence — the emitted trace is.
    return {"status": "ok", "tools_called": len(tools)}
