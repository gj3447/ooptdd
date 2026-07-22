"""DeepEval + ooptdd — answer quality AND arrival proof on one test case.

Run it:  pytest examples/integrations/test_deepeval_arrival.py -s
(needs `pip install deepeval`; skips cleanly without it — verified against
deepeval 4.0.7's real evaluate() loop, 2026-07-22)
"""
from __future__ import annotations

import pytest

pytest.importorskip("deepeval")

from ooptdd.backends.memory import MemoryBackend, reset  # noqa: E402
from ooptdd.integrations import make_arrival_metric  # noqa: E402


def test_agent_answer_and_arrival_judged_together():
    reset()
    backend = MemoryBackend()
    cid = "deepeval-demo-1"
    # the "agent" claims success AND actually emits its side effect:
    backend.ship([{"event": "order.shipped", "cid": cid,
                   "correlation_id": cid, "cycle_id": cid}])

    metric = make_arrival_metric(
        {"cid": cid, "expect": [{"event": "order.shipped", "op": "gte", "target": 1}]},
        backend=backend)
    from deepeval.test_case import LLMTestCase
    case = LLMTestCase(input="Ship order 42", actual_output="Order 42 shipped!")
    score = metric.measure(case)
    assert score == 1.0 and metric.is_successful()

    # the counter-case the LLM-judge metrics cannot see: a great answer whose
    # side effect never landed
    lying = make_arrival_metric(
        {"cid": "deepeval-demo-empty",
         "expect": [{"event": "order.shipped", "op": "gte", "target": 1}]},
        backend=backend)
    assert lying.measure(case) == 0.0 and not lying.is_successful()
    reset()
