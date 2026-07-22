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

Zero new hard dependencies; each bridge imports its platform lazily.
"""
from .deepeval_metric import make_arrival_metric
from .verdict_export import emit_verdict_event, verdict_span_attributes

__all__ = ["make_arrival_metric", "emit_verdict_event", "verdict_span_attributes"]
