"""Optional OpenTelemetry GenAI helpers, ontology presets, and integrations.

Importing this package is inert. Public helpers are loaded only when their attribute
is explicitly requested, so a plain framework import cannot activate a domain preset.
"""

from __future__ import annotations

from importlib import import_module
from types import MappingProxyType

_EXPORT_MODULES = MappingProxyType(
    {
        "execute_tool_event": ".events",
        "invoke_agent_event": ".events",
        "span_to_event": ".openllmetry",
        "spans_to_events": ".openllmetry",
        "gen_ai_ontology": ".semconv",
        "gen_ai_ontology_1_41": ".semconv",
        "make_arrival_metric": ".integrations",
        "emit_verdict_event": ".integrations",
        "verdict_span_attributes": ".integrations",
    }
)

__all__ = (
    "execute_tool_event",
    "gen_ai_ontology",
    "gen_ai_ontology_1_41",
    "invoke_agent_event",
    "make_arrival_metric",
    "span_to_event",
    "spans_to_events",
    "emit_verdict_event",
    "verdict_span_attributes",
    "ontology_presets",
)


def ontology_presets():
    """Return explicit ontology factories without mutating any base registry."""
    from .semconv import gen_ai_ontology, gen_ai_ontology_1_41

    return MappingProxyType(
        {
            "gen_ai@1.30": gen_ai_ontology,
            "gen_ai@1.41": gen_ai_ontology_1_41,
        }
    )


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    return getattr(import_module(module_name, __name__), name)
