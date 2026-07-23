"""OpenTelemetry GenAI semantic conventions — a built-in verify vocabulary (Tier-2 #8).

ooptdd's gate/verify asserts on events *by name and attribute*, which needs a stable,
shared vocabulary. Rather than invent event/attribute names for agent telemetry, we
adopt OpenTelemetry's **GenAI semantic conventions** (``gen_ai.*``): the operation
enum (``chat`` / ``execute_tool`` / ``invoke_agent`` / …) and attribute names
(``gen_ai.tool.name``, ``gen_ai.request.model``, ``gen_ai.provider.name`` — whose enum
includes ``anthropic``). An assertion can then reference an external standard.

⚠ The GenAI conventions are **Development / experimental** — attribute names churn
between releases. So this preset is **version-pinned** (``SEMCONV_VERSION``); bump it
deliberately when you upgrade. The *stable* pieces ooptdd leans on are OTLP (write)
and W3C trace context (``trace_id``/``span_id`` correlation, see
``ooptdd.model.with_trace_context``), not the experimental attribute set.

Practical ooptdd mapping: emit one structured event per agent operation, named by the
operation (``event = "gen_ai.execute_tool"``), carrying the ``gen_ai.*`` attributes.
``gen_ai_ontology()`` returns an :class:`~ooptdd.ontology.Ontology` that makes a
missing ``gen_ai.tool.name`` on an ``execute_tool`` (etc.) RED.

Refs: https://opentelemetry.io/docs/specs/semconv/gen-ai/ (spans + attribute registry).
"""
from __future__ import annotations

from .ontology import Ontology

#: The semconv revision this preset was written against. Pin it; bump on upgrade.
SEMCONV_VERSION = "1.30.0-experimental"

#: gen_ai.operation.name enum (the agent operations we model as event names).
GEN_AI_OPERATIONS = (
    "chat",
    "generate_content",
    "text_completion",
    "embeddings",
    "execute_tool",
    "create_agent",
    "invoke_agent",
    "invoke_workflow",
)

#: gen_ai.provider.name enum (representative; includes anthropic per the registry).
GEN_AI_PROVIDERS = (
    "anthropic", "openai", "azure.ai.openai", "aws.bedrock", "gcp.gemini",
    "cohere", "mistral_ai", "x_ai", "groq", "deepseek", "perplexity", "ibm.watsonx.ai",
)

# event-type name -> required gen_ai.* attributes (the load-bearing invariant per op).
_REQUIRED = {
    "gen_ai.chat": ["gen_ai.provider.name", "gen_ai.request.model"],
    "gen_ai.generate_content": ["gen_ai.provider.name", "gen_ai.request.model"],
    "gen_ai.text_completion": ["gen_ai.provider.name", "gen_ai.request.model"],
    "gen_ai.embeddings": ["gen_ai.request.model"],
    "gen_ai.execute_tool": ["gen_ai.tool.name"],
    "gen_ai.create_agent": ["gen_ai.agent.id"],
    "gen_ai.invoke_agent": ["gen_ai.agent.id"],
    "gen_ai.invoke_workflow": [],
}


def gen_ai_ontology(*, closed_world: bool = False) -> Ontology:
    """Build the version-pinned GenAI ontology.

    ``closed_world`` (default False so it composes with your own event types): when True,
    an in-scope event whose name is not a ``gen_ai.*`` type is reported as drift.
    """
    event_types = {}
    for name, required in _REQUIRED.items():
        constraints = {
            "gen_ai.provider.name": {"enum": list(GEN_AI_PROVIDERS)},
            "gen_ai.usage.input_tokens": {"type": "int", "min": 0},
            "gen_ai.usage.output_tokens": {"type": "int", "min": 0},
        }
        event_types[name] = {
            "required": required,
            "constraints": {k: v for k, v in constraints.items()
                            if k in required or k.startswith("gen_ai.usage")},
            "description": f"OTel GenAI semconv {SEMCONV_VERSION}: {name}",
        }
    return Ontology.from_dict({"event_types": event_types, "closed_world": closed_world})


# ── track 2: gen_ai@1.41 — the final in-repo state before the repo split ───────
# Verified 2026-07-22 against the open-telemetry/semantic-conventions clone
# (main @ fd417dfe): gen_ai.* moved OUT to semantic-conventions-genai at core
# v1.42.0 (CHANGELOG.md), with the final definitions preserved in
# model/gen-ai/deprecated/{registry,spans,events}-deprecated.yaml. This preset
# absorbs that certified ceiling; the LIVING genai repo is a follow-up pin.
#
# Dual-track rule: the ``gen_ai`` preset above is FROZEN (consumers pinned to it
# must never see drift — tests/test_semconv_1_41.py pins its vocabulary). Version
# bumps ADD a ``gen_ai@<ver>`` preset; they never edit an existing one.

SEMCONV_VERSION_1_41 = ("1.41.0-development (final in-repo state; moved to "
                        "semantic-conventions-genai at core v1.42)")

#: 9 operations: retrieval added v1.40.0, invoke_workflow v1.41.0
#: (registry-deprecated.yaml:914-965).
GEN_AI_OPERATIONS_1_41 = (
    "chat",
    "generate_content",
    "text_completion",
    "embeddings",
    "retrieval",
    "execute_tool",
    "create_agent",
    "invoke_agent",
    "invoke_workflow",
)

#: The full 15-member gen_ai.provider.name enum (registry-deprecated.yaml:274-373).
#: Note ``x_ai`` — the old gen_ai.system enum used ``xai``; the rename is enforced.
GEN_AI_PROVIDERS_1_41 = (
    "openai", "gcp.gen_ai", "gcp.vertex_ai", "gcp.gemini", "anthropic", "cohere",
    "azure.ai.inference", "azure.ai.openai", "ibm.watsonx.ai", "aws.bedrock",
    "perplexity", "x_ai", "deepseek", "groq", "mistral_ai",
)

# Requirement levels per spans-deprecated.yaml: gen_ai.provider.name is REQUIRED on
# every client-kind operation (inference/embeddings/retrieval/create_agent/
# invoke_agent); request.model is only conditionally required ("If available"), so it
# stays flat-required only where the older preset already demanded it (inference).
# execute_tool (internal kind) requires gen_ai.tool.name (span name
# "execute_tool {gen_ai.tool.name}"). gen_ai.evaluation.result (events-deprecated
# .yaml:376-420, since v1.38): evaluation.name required; score/explanation attached.
_REQUIRED_1_41 = {
    "gen_ai.chat": ["gen_ai.provider.name", "gen_ai.request.model"],
    "gen_ai.generate_content": ["gen_ai.provider.name", "gen_ai.request.model"],
    "gen_ai.text_completion": ["gen_ai.provider.name", "gen_ai.request.model"],
    "gen_ai.embeddings": ["gen_ai.provider.name"],
    "gen_ai.retrieval": ["gen_ai.provider.name"],
    "gen_ai.execute_tool": ["gen_ai.tool.name"],
    "gen_ai.create_agent": ["gen_ai.provider.name", "gen_ai.agent.id"],
    "gen_ai.invoke_agent": ["gen_ai.provider.name", "gen_ai.agent.id"],
    "gen_ai.invoke_workflow": [],
    "gen_ai.evaluation.result": ["gen_ai.evaluation.name"],
}

#: Token-usage expansion (registry-deprecated.yaml:577-647): int >= 0, like the
#: base pair. (Anthropic note: input_tokens = input + cache_read + cache_creation.)
_TOKEN_ATTRS_1_41 = (
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_creation.input_tokens",
    "gen_ai.usage.reasoning.output_tokens",
)


def gen_ai_ontology_1_41(*, closed_world: bool = False) -> Ontology:
    """Build the ``gen_ai@1.41`` ontology (see the dual-track note above)."""
    event_types = {}
    for name, required in _REQUIRED_1_41.items():
        constraints = {"gen_ai.provider.name": {"enum": list(GEN_AI_PROVIDERS_1_41)}}
        for attr in _TOKEN_ATTRS_1_41:
            constraints[attr] = {"type": "int", "min": 0}
        if name == "gen_ai.evaluation.result":
            constraints["gen_ai.evaluation.score.value"] = {"type": "number"}
        event_types[name] = {
            "required": required,
            "constraints": constraints,
            "description": f"OTel GenAI semconv {SEMCONV_VERSION_1_41}: {name}",
        }
    return Ontology.from_dict({"event_types": event_types, "closed_world": closed_world})


# Self-register as the ``gen_ai`` built-in preset. Inversion seam (see
# :meth:`ooptdd.ontology.Ontology.register_preset`): this preset depends on the core,
# the core does NOT depend on the preset — so ``ontology.py`` never imports ``semconv``
# and the module-import graph stays acyclic (``ooptdd.ontology`` is a pure sink).
Ontology.register_preset("gen_ai", gen_ai_ontology)
Ontology.register_preset("gen_ai@1.41", gen_ai_ontology_1_41)
