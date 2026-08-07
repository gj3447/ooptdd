"""Contract tests for the effect-shell / functional gate-kernel boundary."""

from __future__ import annotations

import copy
import inspect
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from uuid import UUID

import pytest

import ooptdd.engine.gate as gate
from ooptdd.domain.ontology import EventType, Ontology
from ooptdd.domain.ports import ProbeResult
from ooptdd.engine.gate_kernel import judge_events
from ooptdd.engine.gate_ontology import EventTypeSnapshot, OntologySnapshot
from ooptdd.engine.gate_values import (
    ExternalObservation,
    GateEvaluation,
    GatePolicy,
    GateSource,
    GateVerdict,
    freeze_value,
    thaw_value,
)


def _handlers():
    return gate.CHECK_REGISTRY.snapshot(default=gate._eval_count)


def _evaluation(
    spec,
    events,
    *,
    registry=None,
    strength_by_key=None,
    external_observations=None,
    ontology=None,
    captured_check_results=None,
):
    evaluation = GateEvaluation.capture(
        spec,
        events,
        policy=GatePolicy(),
        source=GateSource(cid="functional-core", reachable=True),
        registry=_handlers() if registry is None else registry,
        strength_by_key=(gate._STRENGTH_BY_KEY if strength_by_key is None else strength_by_key),
        external_observations=external_observations,
        ontology=ontology,
    )
    return (
        replace(evaluation, captured_check_results=captured_check_results)
        if captured_check_results is not None
        else evaluation
    )


def _external_verdict(value, want, **external_fields):
    spec = {
        "expect": [
            {
                "external": {
                    "kind": "db_row",
                    "selector": {"id": 7},
                    "want": want,
                    **external_fields,
                }
            }
        ]
    }
    return judge_events(
        _evaluation(
            spec,
            [],
            external_observations={0: ExternalObservation(reachable=True, value=value)},
        )
    ).as_dict()


def test_clean_package_import_keeps_trajectory_strength_registry_immutable():
    program = """
import ooptdd
from ooptdd.engine import trajectory
from ooptdd.engine.gate_rules import _STRENGTH_BY_KEY

expected = {
    "tool_calls": "value-pinned",
    "forbidden_tools": "forbid",
    "forbidden_tool_calls": "forbid",
    "aggregate": "threshold",
}
before = dict(_STRENGTH_BY_KEY)
assert all(_STRENGTH_BY_KEY[key] == value for key, value in expected.items())
try:
    _STRENGTH_BY_KEY["trajectory_test"] = "value-pinned"
except TypeError:
    pass
else:
    raise AssertionError("trajectory strength registry accepted mutation")
assert dict(_STRENGTH_BY_KEY) == before
"""

    completed = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_capture_snapshots_spec_events_and_registry_before_judgement():
    def captured_handler(events, rule, ctx):
        del ctx
        wanted = rule["snapshot_check"]["payload"]["state"]
        seen = events[0]["payload"]["state"]
        return {"passed": seen == wanted, "got": 1, "seen": seen}

    def replacement_handler(events, rule, ctx):  # pragma: no cover - must stay unreachable
        del events, rule, ctx
        raise AssertionError("judge read the caller's mutated registry")

    spec = {
        "expect": [
            {
                "snapshot_check": {"payload": {"state": "captured"}},
                "events": ["arrival"],
            }
        ]
    }
    events = [{"event": "arrival", "payload": {"state": "captured"}, "_timestamp": 1}]
    registry = {"snapshot_check": captured_handler}
    strengths = {"snapshot_check": "value-pinned"}
    evaluation = _evaluation(
        spec,
        events,
        registry=registry,
        strength_by_key=strengths,
    )

    spec["expect"][0]["snapshot_check"]["payload"]["state"] = "mutated"
    spec["expect"].append({"snapshot_check": {"payload": {"state": "extra"}}})
    events[0]["event"] = "mutated"
    events[0]["payload"]["state"] = "mutated"
    registry["snapshot_check"] = replacement_handler
    strengths["snapshot_check"] = "existence-only"

    result = judge_events(evaluation).as_dict()

    assert result["ok"] is True
    assert len(result["checks"]) == 1
    assert result["checks"][0]["seen"] == "captured"
    assert result["checks"][0]["strength"] == "value-pinned"
    assert result["scope"]["unasserted_observed"] == []


def test_judging_the_same_captured_evaluation_twice_is_deterministic():
    evaluation = _evaluation(
        {"expect": [{"present": [{"event": "ready", "where": {"phase": "done"}}]}]},
        [{"event": "ready", "phase": "done", "_timestamp": 2}],
    )

    first = judge_events(evaluation)
    second = judge_events(evaluation)

    assert first.data == second.data
    assert first.as_dict() == second.as_dict()


def test_direct_gate_verdict_data_is_deeply_immutable_and_thaws_independently():
    verdict = GateVerdict(
        {
            "ok": True,
            "checks": [
                {
                    "passed": True,
                    "details": {"path": ["capture", {"state": "frozen"}]},
                }
            ],
        }
    )

    with pytest.raises(TypeError):
        verdict.data["ok"] = False
    with pytest.raises(TypeError):
        verdict.data["checks"][0]["passed"] = False
    with pytest.raises(TypeError):
        verdict.data["checks"][0]["details"]["path"][1]["state"] = "changed"
    with pytest.raises(FrozenInstanceError):
        verdict.data = {}

    thawed = verdict.as_dict()
    thawed["checks"][0]["details"]["path"][1]["state"] = "changed"
    assert verdict.as_dict()["checks"][0]["details"]["path"][1]["state"] == "frozen"


def test_direct_gate_evaluation_is_deeply_immutable():
    spec = {"expect": [{"present": [{"event": "ready"}]}]}
    events = [{"event": "ready", "payload": {"state": "captured"}}]
    evaluation = GateEvaluation(
        spec=spec,
        events=tuple(events),
        policy=GatePolicy(),
        source=GateSource(cid="functional-core", reachable=True),
        registry=_handlers(),
        strength_by_key=gate._STRENGTH_BY_KEY,
        external_observations={},
    )

    spec["expect"][0]["present"][0]["event"] = "mutated"
    events[0]["payload"]["state"] = "mutated"

    assert evaluation.spec["expect"][0]["present"][0]["event"] == "ready"
    assert evaluation.events[0]["payload"]["state"] == "captured"
    with pytest.raises(TypeError):
        evaluation.spec["expect"][0]["present"][0]["event"] = "changed"
    with pytest.raises(TypeError):
        evaluation.events[0]["payload"]["state"] = "changed"


def test_public_gate_values_reject_malformed_root_shapes():
    common = {
        "policy": GatePolicy(),
        "source": GateSource(cid="functional-core", reachable=True),
        "registry": _handlers(),
        "strength_by_key": gate._STRENGTH_BY_KEY,
        "external_observations": {},
    }

    with pytest.raises(TypeError, match="spec must be a mapping"):
        GateEvaluation(spec=[], events=(), **common)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="tuple of mappings"):
        GateEvaluation(spec={}, events=("bad",), **common)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="root must be a mapping"):
        GateVerdict([])  # type: ignore[arg-type]


def test_direct_ontology_snapshots_reject_inconsistent_shapes():
    event_type = EventTypeSnapshot("actual", (), {}, True)

    with pytest.raises(TypeError, match="matching names"):
        OntologySnapshot({"declared": event_type}, False)
    with pytest.raises(TypeError, match="map text to mappings"):
        EventTypeSnapshot("event", (), {"value": "not-a-rule"}, True)


def test_custom_handler_result_dict_is_not_decorated_or_mutated_in_place():
    handler_result = {
        "passed": True,
        "got": 1,
        "details": {"evidence": ["original"]},
    }
    before = copy.deepcopy(handler_result)

    def custom_handler(events, rule, ctx):
        del events, rule, ctx
        return handler_result

    verdict = judge_events(
        _evaluation(
            {"expect": [{"custom": True, "label": "custom-label"}]},
            [],
            registry={"custom": custom_handler},
            strength_by_key={"custom": "value-pinned"},
        )
    )

    assert handler_result == before
    assert "optional" not in handler_result
    assert "strength" not in handler_result
    assert verdict.as_dict()["checks"][0]["label"] == "custom-label"


def test_each_custom_handler_gets_isolated_event_and_rule_inputs():
    seen = []

    def mutating_handler(events, rule, ctx):
        del ctx
        seen.append((events[0]["payload"]["state"], rule["isolated"]["turn"]))
        events[0]["payload"]["state"] = "mutated-by-handler"
        rule["isolated"]["turn"] = 99
        return {"passed": True, "got": 1}

    evaluation = _evaluation(
        {
            "expect": [
                {"isolated": {"turn": 1}},
                {"isolated": {"turn": 2}},
            ]
        },
        [{"event": "ready", "payload": {"state": "captured"}}],
        registry={"isolated": mutating_handler},
        strength_by_key={"isolated": "value-pinned"},
    )

    verdict = judge_events(evaluation).as_dict()

    assert verdict["ok"] is True
    assert seen == [("captured", 1), ("captured", 2)]
    assert evaluation.events[0]["payload"]["state"] == "captured"
    assert evaluation.spec["expect"][1]["isolated"]["turn"] == 2


def test_captured_custom_result_replays_without_reinvoking_the_handler():
    def forbidden_handler(events, rule, ctx):
        del events, rule, ctx
        raise AssertionError("functional replay invoked an already captured extension")

    evaluation = _evaluation(
        {"expect": [{"custom": True}]},
        [],
        registry={"custom": forbidden_handler},
        strength_by_key={"custom": "value-pinned"},
        captured_check_results={0: {"passed": True, "got": 1}},
    )

    first = judge_events(evaluation).as_dict()
    second = judge_events(evaluation).as_dict()

    assert first == second
    assert first["ok"] is True


@pytest.mark.parametrize("passed", [None, 0, 1, "true", [], {}])
def test_custom_handler_rejects_non_bool_passed_values(passed):
    def invalid_handler(events, rule, ctx):
        del events, rule, ctx
        return {"passed": passed}

    with pytest.raises(ValueError, match="exact bool 'passed'"):
        judge_events(
            _evaluation(
                {"expect": [{"invalid": True}]},
                [],
                registry={"invalid": invalid_handler},
                strength_by_key={"invalid": "value-pinned"},
            )
        )


def test_explicit_external_observation_matches_compatibility_shell_verdict():
    spec = {
        "expect": [
            {
                "external": {
                    "kind": "db_row",
                    "selector": {"table": "payments", "id": 7},
                    "want": 42,
                }
            }
        ]
    }
    observation = ExternalObservation(
        reachable=True,
        value=42,
        complete=True,
        separate_source=True,
        derived_identity="postgres://judge",
    )
    policy = GatePolicy()
    source = GateSource(cid="functional-core", reachable=True)
    handlers = _handlers()
    evaluation = GateEvaluation.capture(
        spec,
        [],
        policy=policy,
        source=source,
        registry=handlers,
        strength_by_key=gate._STRENGTH_BY_KEY,
        external_observations={0: observation},
    )

    pure = judge_events(evaluation).as_dict()
    compatible = gate.evaluate_events(
        spec,
        [],
        reachable=True,
        cid="functional-core",
        policy=policy,
        registry=handlers,
        strength_by_key=gate._STRENGTH_BY_KEY,
        external_observations={0: observation},
    )

    assert pure == compatible
    assert pure["ok"] is True


def test_external_observation_nested_payload_is_captured_deeply():
    payload = {"row": {"amounts": [40, 2]}}
    observation = ExternalObservation(reachable=True, value=payload)
    evaluation = _evaluation(
        {
            "expect": [
                {
                    "external": {
                        "kind": "db_row",
                        "selector": {"id": 7},
                        "want": {"row": {"amounts": [40, 2]}},
                    }
                }
            ]
        },
        [],
        external_observations={0: observation},
    )

    payload["row"]["amounts"][1] = 999

    with pytest.raises(TypeError):
        observation.value["row"]["amounts"][1] = 999
    verdict = judge_events(evaluation).as_dict()
    assert verdict["ok"] is True
    assert verdict["checks"][0]["value"] == {"row": {"amounts": [40, 2]}}


def test_gate_evaluation_snapshots_a_real_ontology_before_source_mutation():
    event_type = EventType(
        name="payment",
        required=["amount"],
        constraints={"amount": {"type": "number", "min": 0}},
        additional_properties=False,
    )
    ontology = Ontology(types={"payment": event_type}, closed_world=True)
    evaluation = _evaluation(
        {"expect": [{"conforms": "payment"}]},
        [{"event": "payment", "amount": 5}],
        ontology=ontology,
    )

    event_type.required.append("currency")
    event_type.constraints["amount"]["min"] = 10
    ontology.types.clear()
    ontology.closed_world = False

    verdict = judge_events(evaluation).as_dict()
    assert verdict["ok"] is True
    assert verdict["checks"][0]["checked"] == 1
    assert evaluation.ontology.closed_world is True
    assert evaluation.ontology.get("payment").required == ("amount",)


def test_ontology_capture_rejects_custom_validation_or_lookup_semantics():
    class CustomEventType(EventType):
        def validate(self, event):
            del event
            return ["custom invariant"]

    class CustomOntology(Ontology):
        def get(self, name):
            del name
            return None

    with pytest.raises(TypeError, match="custom validation semantics"):
        _evaluation(
            {"expect": [{"conforms": "payment"}]},
            [{"event": "payment"}],
            ontology=Ontology(types={"payment": CustomEventType("payment")}),
        )
    with pytest.raises(TypeError, match="custom lookup semantics"):
        _evaluation(
            {"expect": [{"conforms": "payment"}]},
            [{"event": "payment"}],
            ontology=CustomOntology(types={"payment": EventType("payment")}),
        )

    patched = EventType("payment")
    patched.validate = lambda event: ["patched"]  # type: ignore[method-assign]
    with pytest.raises(TypeError, match="custom validation semantics"):
        _evaluation(
            {"expect": [{"conforms": "payment"}]},
            [{"event": "payment"}],
            ontology=Ontology(types={"payment": patched}),
        )


def test_inherited_stock_event_type_and_enum_messages_survive_capture():
    class InheritedEventType(EventType):
        pass

    event_type = InheritedEventType(
        "state", constraints={"value": {"enum": ["ready", "done"]}}
    )
    event = {"event": "state", "value": "invalid"}
    snapshot = OntologySnapshot.capture(Ontology(types={"state": event_type}))

    assert snapshot.get("state") is not None
    assert snapshot.get("state").validate(event) == event_type.validate(event)

    nested = EventType(
        "nested", constraints={"value": {"enum": [["ready", "done"]]}}
    )
    nested_snapshot = OntologySnapshot.capture(Ontology(types={"nested": nested}))
    assert nested_snapshot.get("nested").validate(
        {"event": "nested", "value": ["ready", "done"]}
    ) == []


@pytest.mark.parametrize(
    "value",
    [
        Decimal("42.125"),
        Fraction(337, 8),
        date(2026, 8, 7),
        UUID("12345678-1234-5678-1234-567812345678"),
        Path("artifacts/gate.json"),
    ],
)
def test_supported_immutable_external_values_keep_their_type(value):
    verdict = _external_verdict(value, value)

    assert verdict["ok"] is True
    assert verdict["checks"][0]["value"] == value
    assert type(verdict["checks"][0]["value"]) is type(value)


def test_freeze_thaw_preserves_container_kinds_recursively():
    value = {
        "list": [(1, 2), {3, 4}, frozenset({5, 6})],
        "tuple": ([7, 8], {9, 10}),
    }

    thawed = thaw_value(freeze_value(value))

    assert type(thawed["list"]) is list
    assert type(thawed["list"][0]) is tuple
    assert type(thawed["list"][1]) is set
    assert type(thawed["list"][2]) is frozenset
    assert type(thawed["tuple"]) is tuple
    assert type(thawed["tuple"][0]) is list
    assert type(thawed["tuple"][1]) is set


def test_capture_rejects_cyclic_and_unsupported_nested_values():
    cyclic = []
    cyclic.append(cyclic)

    with pytest.raises(ValueError, match="cyclic"):
        ExternalObservation(reachable=True, value=cyclic)
    with pytest.raises(TypeError, match="unsupported captured value"):
        GateVerdict({"unsupported": object()})


@pytest.mark.parametrize("tolerance", [-0.01, "invalid", float("inf"), True])
def test_external_comparison_rejects_invalid_or_negative_tolerance(tolerance):
    with pytest.raises(ValueError, match="external tolerance"):
        _external_verdict(42, 42, tol=tolerance)


@pytest.mark.parametrize(
    ("value", "want", "expected"),
    [
        (42, 42.0, True),
        ("42", "42", True),
        ("42", 42, False),
        (42, "42", False),
    ],
)
def test_external_equality_preserves_string_numeric_parity(value, want, expected):
    verdict = _external_verdict(value, want)

    assert verdict["ok"] is expected
    assert verdict["checks"][0]["passed"] is expected


@pytest.mark.parametrize(
    "numeric_type",
    [Decimal, Fraction],
)
def test_external_comparison_preserves_exact_values_above_float_precision(numeric_type):
    lower = numeric_type(2**53)
    higher = numeric_type(2**53 + 1)

    assert _external_verdict(lower, higher)["ok"] is False
    assert _external_verdict(higher, lower, op=">")["ok"] is True
    assert _external_verdict(lower, higher, tol=Fraction(1, 2))["ok"] is False
    assert _external_verdict(lower, higher, tol=1)["ok"] is True


def test_unhashable_callable_custom_handler_is_captured_by_identity():
    class Handler:
        __hash__ = None

        def __eq__(self, other):
            return self is other

        def __call__(self, events, rule, ctx):
            del events, rule, ctx
            return {"passed": True, "got": 1}

    verdict = gate.evaluate_events(
        {"cid": "functional-core", "expect": [{"custom": {}}]},
        [],
        reachable=True,
        registry={"custom": Handler()},
    )

    assert verdict["ok"] is True


def test_judge_events_signature_has_no_route_to_call_an_external_probe():
    assert list(inspect.signature(judge_events).parameters) == ["evaluation"]
    assert "probe" not in inspect.signature(GateEvaluation.capture).parameters


def test_probe_selector_mutation_cannot_change_spec_or_shift_observation_indexes():
    class MutatingProbe:
        def __init__(self):
            self.selectors = []

        def probe(self, kind, selector, cid):
            del cid
            self.selectors.append(copy.deepcopy(selector))
            value = selector["id"]
            selector.clear()
            selector["id"] = 999
            return ProbeResult(reachable=True, value=value, derived_identity=kind)

    spec = {
        "cid": "functional-core",
        "expect": [
            {"external": {"kind": "first", "selector": {"id": 1}, "want": 1}},
            {"external": {"kind": "second", "selector": {"id": 2}, "want": 2}},
        ],
    }
    before = copy.deepcopy(spec)
    probe = MutatingProbe()

    verdict = gate.evaluate_events(spec, [], reachable=True, probe=probe)

    assert verdict["ok"] is True
    assert [check["value"] for check in verdict["checks"]] == [1, 2]
    assert [check["selector"] for check in verdict["checks"]] == [{"id": 1}, {"id": 2}]
    assert probe.selectors == [{"id": 1}, {"id": 2}]
    assert spec == before
