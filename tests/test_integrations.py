"""Eval-platform bridges — DeepEval arrival metric + verdict export.

deepeval itself is NOT a dependency: the metric factory is exercised against a
minimal fake `deepeval.metrics` module, and the no-deepeval path must raise a
helpful ImportError. The verdict export is dogfooded: the emitted
`ooptdd.verdict` event is itself arrival-asserted by a second gate.
"""
import sys
import types

import pytest

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.integrations import (
    emit_verdict_event,
    make_arrival_metric,
    verdict_span_attributes,
)

CID = "bridge-cid"


@pytest.fixture
def mem():
    reset()
    b = MemoryBackend()
    yield b
    reset()


@pytest.fixture
def fake_deepeval(monkeypatch):
    """The minimal surface make_arrival_metric touches: deepeval.metrics.BaseMetric."""
    if "deepeval" in sys.modules and not isinstance(
            sys.modules["deepeval"], types.SimpleNamespace):
        pytest.skip("real deepeval installed; fake not needed")
    pkg = types.ModuleType("deepeval")
    metrics = types.ModuleType("deepeval.metrics")

    class BaseMetric:  # the real one is richer; the factory only subclasses it
        pass

    metrics.BaseMetric = BaseMetric
    pkg.metrics = metrics
    monkeypatch.setitem(sys.modules, "deepeval", pkg)
    monkeypatch.setitem(sys.modules, "deepeval.metrics", metrics)
    return metrics


def _ship(b, event, **attrs):
    b.ship([{"event": event, "cid": CID, "correlation_id": CID, "cycle_id": CID, **attrs}])


def _spec(expect):
    return {"cid": CID, "expect": expect}


def test_arrival_metric_green(mem, fake_deepeval):
    _ship(mem, "order.shipped")
    metric = make_arrival_metric(
        _spec([{"event": "order.shipped", "op": "gte", "target": 1}]), backend=mem)
    score = metric.measure(object())
    assert score == 1.0 and metric.is_successful()
    assert metric.error is None and "arrived" in metric.reason


def test_arrival_metric_red_partial_score(mem, fake_deepeval):
    _ship(mem, "order.shipped")
    metric = make_arrival_metric(
        _spec([{"event": "order.shipped", "op": "gte", "target": 1},
               {"event": "invoice.sent", "op": "gte", "target": 1}]), backend=mem)
    assert metric.measure(object()) == 0.5
    assert not metric.is_successful()


def test_arrival_metric_infra_errors_not_confident_fail(fake_deepeval):
    class DeadBackend:
        default_lookback_s = 60
        default_future_buffer_s = 0

        def ship(self, events):
            pass

        def query(self, cid, *, since_us, until_us):
            from ooptdd.backends.base import QueryResult
            return QueryResult(reachable=False)

    metric = make_arrival_metric(
        _spec([{"event": "x", "op": "gte", "target": 1}]), backend=DeadBackend())
    assert metric.measure(object()) == 0.0
    assert metric.error and "inconclusive" in metric.error


def test_missing_deepeval_raises_with_install_hint(monkeypatch):
    for name in [m for m in sys.modules if m == "deepeval" or m.startswith("deepeval.")]:
        monkeypatch.delitem(sys.modules, name)
    import builtins
    real_import = builtins.__import__

    def block(name, *a, **kw):
        if name.startswith("deepeval"):
            raise ImportError("No module named 'deepeval'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", block)
    with pytest.raises(ImportError, match="pip install deepeval"):
        make_arrival_metric(_spec([]))


# ── verdict export ─────────────────────────────────────────────────────────────


def test_emit_verdict_event_is_itself_arrival_assertable(mem):
    from ooptdd.engine.gate import evaluate
    _ship(mem, "boot")
    res = evaluate(mem, _spec([{"event": "boot", "op": "gte", "target": 1}]))
    ev = emit_verdict_event(mem, res)
    assert ev["verdict"] == "present" and ev["annotator_kind"] == "CODE"
    # the verdict event honors the envelope contract (spec_version/service/level).
    assert ev["spec_version"] and ev["service"] == "ooptdd.gate" and ev["level"] == "INFO"


def test_verdict_event_does_not_poison_a_closed_world_conforms_gate(mem):
    """grill HIGH-2: a closed-world `conforms:` gate over the cid must NOT flip RED after
    export — ooptdd.* framework meta-events are exempt from closed-world drift."""
    from ooptdd.domain.ontology import Ontology
    from ooptdd.engine.gate import evaluate
    onto = Ontology.from_dict({"closed_world": True, "event_types": {"boot": {}}})
    _ship(mem, "boot")
    before = evaluate(mem, _spec([{"conforms": "boot", "closed_world": True}]), ontology=onto)
    assert before["ok"]
    emit_verdict_event(mem, before)
    after = evaluate(mem, _spec([{"conforms": "boot", "closed_world": True}]), ontology=onto)
    assert after["ok"], "ooptdd.verdict must not be closed-world drift"


def test_verdict_event_IS_invasive_to_a_pin_service_gate_honest(mem):
    """grill HIGH-2, honest recant: pin_service is count(all)==count(pinned) and cannot
    exempt a namespace, so exporting a verdict with a DIFFERENT service DOES flip a
    whole-cid pin_service gate. The library documents this (run strict gates before
    export, or align `service=`). Pin the true behavior instead of dodging it."""
    from ooptdd.engine.gate import evaluate
    mem.ship([{"event": "boot", "cid": CID, "correlation_id": CID, "cycle_id": CID,
               "service": "myapp"}])
    spec = {**_spec([{"event": "boot", "op": "gte", "target": 1}]), "pin_service": "myapp"}
    assert evaluate(mem, spec)["ok"]
    # default service=ooptdd.gate: DOES flip (honest, documented)
    emit_verdict_event(mem, evaluate(mem, spec))
    assert not evaluate(mem, spec)["ok"], "the invasive interaction is real, not hidden"
    # the documented escape hatch: align the verdict's service with the pin
    reset()
    mem2 = MemoryBackend()
    mem2.ship([{"event": "boot", "cid": CID, "correlation_id": CID, "cycle_id": CID,
                "service": "myapp"}])
    emit_verdict_event(mem2, evaluate(mem2, spec), service="myapp")
    assert evaluate(mem2, spec)["ok"], "service= alignment keeps pin_service green"
    # dogfood: the verdict event's own arrival is gated
    res2 = evaluate(mem, _spec([
        {"event": "ooptdd.verdict", "where": {"verdict": "present"}, "op": "gte", "target": 1},
    ]))
    assert res2["ok"]


def test_verdict_span_attributes_flat_and_infra_word(mem):
    from ooptdd.engine.gate import evaluate_events
    res = evaluate_events(_spec([{"event": "x", "op": "gte", "target": 1}]), [],
                          reachable=False, cid=CID)
    attrs = verdict_span_attributes(res)
    assert attrs["ooptdd.verdict"] == "inconclusive"
    assert all(isinstance(v, (str, int, bool)) for v in attrs.values())
    res_red = evaluate_events(_spec([{"event": "x", "op": "gte", "target": 1}]), [],
                              reachable=True, cid=CID)
    attrs_red = verdict_span_attributes(res_red)
    assert attrs_red["ooptdd.verdict"] == "absent" and attrs_red["ooptdd.checks.failed"] == 1
