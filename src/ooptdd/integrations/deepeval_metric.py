"""A DeepEval custom metric that scores ARRIVAL, not answer quality.

DeepEval's agentic metrics judge what the agent *said and chose*; this metric
judges whether the events the run was supposed to emit actually landed in the
store. Put both on one test case and a "great answer" whose side effects never
happened stops being a pass.

Import-guarded: deepeval is looked up only inside :func:`make_arrival_metric`,
so ooptdd keeps zero hard dependencies on eval platforms.

Usage::

    from ooptdd.integrations import make_arrival_metric

    metric = make_arrival_metric(
        {"cid": run_cid, "expect": [{"event": "order.shipped", "op": "gte", "target": 1}]},
        backend=my_backend,          # default: the zero-infra memory backend
    )
    evaluate([LLMTestCase(input=..., actual_output=...)], [metric, AnswerRelevancyMetric()])

Scoring: fraction of gating checks that passed (1.0 = all arrived). INFRA
(store unreachable / truncated read) scores 0 with ``error`` set — surfaced as
an errored metric, NOT a confident failure, mirroring the LTL3 ``inconclusive``.
"""
from __future__ import annotations

from ..backends import get_backend
from ..engine.gate import evaluate


def _gating(checks: list[dict]) -> list[dict]:
    return [c for c in checks if not c.get("optional") and not c.get("pending")]


def make_arrival_metric(gate_spec: dict, *, backend=None, threshold: float = 1.0,
                        name: str = "ooptdd arrival"):
    """Build a DeepEval ``BaseMetric`` subclass instance wrapping an ooptdd gate.

    Raises a clear ImportError (with install hint) if deepeval isn't installed.
    """
    try:
        from deepeval.metrics import BaseMetric
    except ImportError as exc:  # pragma: no cover - exercised via test's fake module
        raise ImportError(
            "make_arrival_metric needs the deepeval package (pip install deepeval); "
            "ooptdd itself does not depend on it") from exc

    resolved_backend = backend or get_backend("memory")

    class ArrivalMetric(BaseMetric):
        """Positive-arrival gate as a DeepEval metric (deterministic, no LLM)."""

        def __init__(self):
            self.threshold = threshold
            self.score = None
            self.success = None
            self.reason = None
            self.error = None
            self.evaluation_model = None  # deterministic: no judge model involved

        # DeepEval calls measure(test_case); the trace evidence lives in the store,
        # keyed by the gate's cid — the test case object is not the evidence.
        def measure(self, test_case, *_args, **_kwargs) -> float:
            res = evaluate(resolved_backend, dict(gate_spec))
            gating = _gating(res.get("checks", []))
            if not res.get("reachable", True) or not res.get("complete", True):
                self.score, self.success = 0.0, False
                self.error = "inconclusive: store unreachable or readback truncated"
                self.reason = self.error
                return self.score
            passed = sum(1 for c in gating if c.get("passed"))
            self.score = passed / len(gating) if gating else 0.0
            self.success = bool(res.get("ok")) and self.score >= self.threshold
            failed = [c for c in gating if not c.get("passed")]
            self.reason = ("all expected events arrived" if not failed else
                           f"{len(failed)} gating check(s) missed arrival")
            return self.score

        async def a_measure(self, test_case, *_args, **_kwargs) -> float:
            return self.measure(test_case)

        def is_successful(self) -> bool:
            return bool(self.success)

        @property
        def __name__(self):
            return name

    return ArrivalMetric()
