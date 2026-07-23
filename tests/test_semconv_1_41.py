"""The ``gen_ai@1.41`` preset — dual-track semconv versioning.

Track 1 (``gen_ai``): the existing preset, FROZEN — consumers pinned to it must
never see its vocabulary drift.
Track 2 (``gen_ai@1.41``): the final in-repo state of the OTel GenAI conventions
before they moved to the separate ``semantic-conventions-genai`` repo at core
v1.42 (verified 2026-07-22 against the semantic-conventions clone @ fd417dfe:
CHANGELOG v1.42.0 + model/gen-ai/deprecated/*.yaml). Deltas absorbed:

- 9 operations: ``retrieval`` (added v1.40) joins the enum.
- ``gen_ai.provider.name`` (the post-1.37 rename of ``gen_ai.system``) required on
  every client-kind operation — including ``embeddings`` (whose ``request.model``
  is only conditionally required in the spec, so it is NOT flat-required here).
- ``gen_ai.evaluation.result`` event (since v1.38): ``gen_ai.evaluation.name``
  required; score.value/score.label/explanation as constraints.
- provider enum is the 15-member registry list (``x_ai``, not the old ``xai``).
- token-usage expansion: cache_read / cache_creation / reasoning token attrs.
"""
from __future__ import annotations

from ooptdd.domain.ontology import Ontology
from ooptdd.domain.semconv import (
    _REQUIRED,
    GEN_AI_OPERATIONS,
    GEN_AI_OPERATIONS_1_41,
    GEN_AI_PROVIDERS_1_41,
    SEMCONV_VERSION_1_41,
)


def _violations(onto, event):
    from ooptdd.domain.ontology import check_conformance
    return check_conformance([event], onto)


# ── track 1: the existing preset is frozen ─────────────────────────────────────
def test_legacy_preset_vocabulary_is_frozen():
    # The freeze test: if this fails, someone drifted the pinned preset instead of
    # adding a new track. Bump by ADDING gen_ai@<ver>, never by editing gen_ai.
    assert GEN_AI_OPERATIONS == (
        "chat", "generate_content", "text_completion", "embeddings",
        "execute_tool", "create_agent", "invoke_agent", "invoke_workflow")
    assert "retrieval" not in GEN_AI_OPERATIONS
    assert _REQUIRED["gen_ai.embeddings"] == ["gen_ai.request.model"]
    assert Ontology.builtin("gen_ai") is not None


# ── track 2: gen_ai@1.41 ───────────────────────────────────────────────────────
def test_1_41_preset_resolves_and_has_nine_operations():
    onto = Ontology.builtin("gen_ai@1.41")
    assert onto is not None
    assert "retrieval" in GEN_AI_OPERATIONS_1_41
    assert len(GEN_AI_OPERATIONS_1_41) == 9
    assert "1.41" in SEMCONV_VERSION_1_41


def test_1_41_provider_enum_is_the_registry_15():
    assert len(GEN_AI_PROVIDERS_1_41) == 15
    assert "x_ai" in GEN_AI_PROVIDERS_1_41 and "xai" not in GEN_AI_PROVIDERS_1_41
    assert {"gcp.gen_ai", "gcp.vertex_ai", "azure.ai.inference"} <= set(GEN_AI_PROVIDERS_1_41)


def test_1_41_execute_tool_still_requires_tool_name():
    onto = Ontology.builtin("gen_ai@1.41")
    res = _violations(onto, {"event": "gen_ai.execute_tool",
                             "gen_ai.provider.name": "anthropic"})
    assert not res["passed"]
    assert any("gen_ai.tool.name" in str(v) for v in res["violations"])


def test_1_41_embeddings_requires_provider_not_model():
    onto = Ontology.builtin("gen_ai@1.41")
    # provider.name present, no request.model -> conformant (model is conditional in spec)
    ok = _violations(onto, {"event": "gen_ai.embeddings",
                            "gen_ai.provider.name": "openai"})
    assert ok["passed"]
    # provider.name missing -> RED
    bad = _violations(onto, {"event": "gen_ai.embeddings",
                             "gen_ai.request.model": "text-embedding-3"})
    assert not bad["passed"]


def test_1_41_retrieval_event_type_exists():
    onto = Ontology.builtin("gen_ai@1.41")
    res = _violations(onto, {"event": "gen_ai.retrieval"})
    assert not res["passed"]  # provider.name required on the client-kind op
    ok = _violations(onto, {"event": "gen_ai.retrieval", "gen_ai.provider.name": "openai"})
    assert ok["passed"]


def test_1_41_evaluation_result_event():
    onto = Ontology.builtin("gen_ai@1.41")
    bad = _violations(onto, {"event": "gen_ai.evaluation.result"})
    assert not bad["passed"]  # gen_ai.evaluation.name is required
    ok = _violations(onto, {"event": "gen_ai.evaluation.result",
                            "gen_ai.evaluation.name": "arrival",
                            "gen_ai.evaluation.score.label": "present"})
    assert ok["passed"]


def test_1_41_token_usage_expansion_constraints():
    onto = Ontology.builtin("gen_ai@1.41")
    bad = _violations(onto, {"event": "gen_ai.chat", "gen_ai.provider.name": "anthropic",
                             "gen_ai.request.model": "claude",
                             "gen_ai.usage.cache_read.input_tokens": -1})
    assert not bad["passed"]  # negative token count violates min 0


def test_1_41_bad_provider_enum_value_is_red():
    onto = Ontology.builtin("gen_ai@1.41")
    bad = _violations(onto, {"event": "gen_ai.chat", "gen_ai.provider.name": "xai",
                             "gen_ai.request.model": "grok-4"})
    assert not bad["passed"]  # old enum member name: the rename is enforced
