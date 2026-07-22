"""Eval-platform bridges — compose with DeepEval/promptfoo/Phoenix, don't compete.

ooptdd deliberately does not do LLM-judge quality scoring (task completion, plan
adherence, answer quality) — DeepEval, Ragas, Phoenix, LangSmith already do. What
they don't do is prove **arrival**: that the expected runtime events landed in an
independent store. These adapters put both in one run:

- :func:`make_arrival_metric` — a DeepEval custom metric whose ``measure()`` runs
  an ooptdd gate, so one DeepEval test case can carry LLM-judge metrics AND an
  arrival proof. Import-guarded: deepeval is never a hard dependency.
- :func:`emit_verdict_event` / :func:`verdict_span_attributes` — export a gate
  verdict as an ``ooptdd.verdict`` structured event / OTel span attributes, so
  trace UIs (Phoenix, LangSmith) display arrival verdicts inline with traces.
  Phoenix's annotation model calls this annotator kind ``CODE``.
- promptfoo: no adapter code needed — a ``python`` assert that calls
  :func:`ooptdd.assert_gate`; worked example in ``examples/integrations/promptfoo/``.
- :mod:`platform_scores` — the verdict as a platform-native score: LangSmith
  categorical feedback kwargs, Langfuse ``POST /api/public/scores`` (CATEGORICAL),
  Phoenix ``CODE`` trace annotations. The three-valued verdict never collapses
  (inconclusive maps to no-number, never 0/0.5).

Zero new hard dependencies; each bridge imports its platform lazily.
"""
from .deepeval_metric import make_arrival_metric
from .platform_scores import (
    langfuse_score_body,
    langsmith_feedback_kwargs,
    phoenix_annotation_payload,
    post_langfuse_score,
    post_phoenix_annotations,
)
from .verdict_export import emit_verdict_event, verdict_span_attributes

__all__ = [
    "make_arrival_metric", "emit_verdict_event", "verdict_span_attributes",
    "langsmith_feedback_kwargs", "langfuse_score_body", "phoenix_annotation_payload",
    "post_langfuse_score", "post_phoenix_annotations",
]
