"""Opt-in builders for version-pinned OpenTelemetry GenAI events.

The correlation id is the W3C trace id, giving storage queries and trace tooling one
caller-supplied identity. Builders copy nested extension attributes so later mutation
of caller-owned values cannot rewrite an already-built event.
"""

from __future__ import annotations

from copy import deepcopy

from ooptdd import correlation_keys

from .semconv import GEN_AI_OPERATIONS

_EVENT_PREFIX = "gen_ai."
_AGENT_ID = "gen_ai.agent.id"
_AGENT_NAME = "gen_ai.agent.name"
_PROVIDER_NAME = "gen_ai.provider.name"
_REQUEST_MODEL = "gen_ai.request.model"
_TOOL_NAME = "gen_ai.tool.name"
_TOOL_CALL_ID = "gen_ai.tool.call.id"


def _snapshot_attrs(attrs: dict) -> dict:
    """Return an independent copy of caller-owned extension attributes."""

    return deepcopy(attrs)


def _gen_ai_event(operation: str, trace_id: str, span_id: str | None, attrs: dict) -> dict:
    """Build one operation event with W3C trace context and correlation aliases."""
    if operation not in GEN_AI_OPERATIONS:
        raise ValueError(f"unknown gen_ai operation {operation!r}; one of {GEN_AI_OPERATIONS}")
    if not isinstance(trace_id, str) or not trace_id:
        raise ValueError("trace_id must be a non-empty string")
    rec = {"event": f"{_EVENT_PREFIX}{operation}", **_snapshot_attrs(attrs)}
    rec["trace_id"] = trace_id
    if span_id is not None:
        rec["span_id"] = span_id
    rec.update(correlation_keys(trace_id))
    rec["cycle_id"] = trace_id
    return rec


def invoke_agent_event(
    *,
    trace_id: str,
    agent_id: str,
    agent_name: str | None = None,
    provider: str | None = None,
    request_model: str | None = None,
    span_id: str | None = None,
    **extra,
) -> dict:
    """Build a ``gen_ai.invoke_agent`` event."""
    attrs: dict = {_AGENT_ID: agent_id}
    if agent_name is not None:
        attrs[_AGENT_NAME] = agent_name
    if provider is not None:
        attrs[_PROVIDER_NAME] = provider
    if request_model is not None:
        attrs[_REQUEST_MODEL] = request_model
    attrs.update(extra)
    return _gen_ai_event("invoke_agent", trace_id, span_id, attrs)


def execute_tool_event(
    *,
    trace_id: str,
    tool_name: str,
    tool_call_id: str | None = None,
    span_id: str | None = None,
    **extra,
) -> dict:
    """Build a ``gen_ai.execute_tool`` event."""
    attrs: dict = {_TOOL_NAME: tool_name}
    if tool_call_id is not None:
        attrs[_TOOL_CALL_ID] = tool_call_id
    attrs.update(extra)
    return _gen_ai_event("execute_tool", trace_id, span_id, attrs)


__all__ = ["execute_tool_event", "invoke_agent_event"]
