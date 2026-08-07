from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from ooptdd_mutation.ouroboros import (
    PROTOCOL_VERSION,
    CycleIdentity,
    CycleSnapshot,
    EffectKind,
    EventKind,
    EventRecord,
    Phase,
    ProtocolBudget,
    ProtocolEvent,
    digest_raw,
    step,
)

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs" / "ouroboros" / "ooptdd_mutation"


def _load(name: str) -> dict:
    return json.loads((DOCS / name).read_text(encoding="utf-8"))


def test_module_decision_stops_before_premature_engine_runtime():
    decision = _load("module-decision.json")
    assert decision["schema_version"] == "engine-decision/v1"
    assert decision["verdict"] == "module"
    assert {"protocol", "durability", "implementation_slices"}.isdisjoint(decision)
    assert decision["promotion_gates"]
    assert decision["falsifiers"]


def test_bounded_policy_names_the_code_budget_and_denies_durability_claims():
    policy = _load("bounded-execution-policy.json")
    assert set(policy["bounds"]) == {item.name for item in fields(ProtocolBudget)}
    assert policy["durability"]["provided"] is False
    assert policy["effects"]["deduplication_key"] == "effect_id"
    assert set(policy["progress"]["caller_owned_bounds"]) == {
        "retry limit",
        "timeout or deadline",
        "no-progress limit",
        "total invocation limit",
    }
    assert "does not" in policy["progress"]["no_progress"]
    assert policy["progress"]["safety_tail_max_records"] == 2
    assert "recovery_required rejects" in policy["progress"]["safety_tail"]


def test_fsm_event_envelopes_match_protocol_event_exactly():
    spec = _load("fsm-spec.json")
    expected_fields = {item.name for item in fields(ProtocolEvent)}
    assert expected_fields == {
        "cycle_id",
        "cycle_identity_sha256",
        "event_id",
        "kind",
        "payload_json",
        "intent_hash",
    }
    for kind in EventKind:
        schema = spec["event_schemas"][kind.name]
        assert set(schema["required"]) == expected_fields
        assert set(schema["properties"]) == expected_fields
        assert schema["additionalProperties"] is False
        assert schema["properties"]["kind"]["const"] == kind.value


def test_receipt_trace_event_shape_remains_replayable():
    assert {item.name for item in fields(EventRecord)} == {
        "cycle_id",
        "cycle_identity_sha256",
        "event_id",
        "kind",
        "payload_json",
        "intent_hash",
        "from_phase",
        "to_phase",
    }


def _run_abstract_case(machine: dict, case: dict) -> tuple[set[str], set[str], bool]:
    state = machine["initial"]
    selected: set[str] = set()
    false_guards: set[str] = set()
    invalid = False
    policy = machine["invalid_event_policy"]
    for trace_step in case["steps"]:
        choices = [
            transition
            for transition in machine["transitions"]
            if transition["from"] == state and transition["event"] == trace_step["event"]
        ]
        choices.sort(key=lambda transition: transition.get("priority", 0))
        for transition in choices:
            guard = transition.get("guard")
            if guard and trace_step["guard_results"].get(guard) is False:
                false_guards.add(guard)
        enabled = [
            transition
            for transition in choices
            if transition.get("guard") is None
            or trace_step["guard_results"].get(transition["guard"]) is True
        ]
        if enabled:
            transition = enabled[0]
            selected.add(transition["id"])
            state = transition["to"]
            effects = transition.get("effects", [])
        else:
            invalid = invalid or not choices
            mode = policy["guard_false"] if choices else policy["mode"]
            effects = [policy["effect"]] if mode == "reject-and-audit" else []
        assert state == trace_step["expected_state"]
        assert effects == trace_step["expected_effects"]
    return selected, false_guards, invalid


def test_fsm_source_and_traces_cover_transitions_guards_and_invalid_events():
    spec = _load("fsm-spec.json")
    traces = _load("fsm-traces.json")
    machines = {machine["id"]: machine for machine in spec["machines"]}
    selected = {machine_id: set() for machine_id in machines}
    false_guards = {machine_id: set() for machine_id in machines}
    invalid = {machine_id: False for machine_id in machines}
    for case in traces["cases"]:
        machine_id = case["machine"]
        case_selected, case_false, case_invalid = _run_abstract_case(machines[machine_id], case)
        selected[machine_id] |= case_selected
        false_guards[machine_id] |= case_false
        invalid[machine_id] |= case_invalid
    for machine_id, machine in machines.items():
        assert selected[machine_id] == {transition["id"] for transition in machine["transitions"]}
        assert false_guards[machine_id] == {
            transition["guard"] for transition in machine["transitions"] if "guard" in transition
        }
        assert invalid[machine_id]


def test_fsm_vocabulary_is_bound_to_the_public_protocol_types():
    spec = _load("fsm-spec.json")
    assert set(spec["event_schemas"]) == {kind.name for kind in EventKind}
    generation = next(machine for machine in spec["machines"] if machine["id"] == "generation")
    state_ids = {state["id"] for state in generation["states"]}
    assert state_ids <= {phase.value for phase in Phase}
    assert set(spec["effects"]) == {kind.name for kind in EffectKind}
    declared_effects = set(spec["effects"])
    assert all(
        set(transition.get("effects", ())) <= declared_effects
        for machine in spec["machines"]
        for transition in machine["transitions"]
    )
    superseded = next(
        transition
        for transition in generation["transitions"]
        if transition["id"] == "seal-superseded"
    )
    assert superseded["effects"] == ["RECORD_TRANSITION"]
    assert (DOCS / "fsm.mmd").read_text(encoding="utf-8").startswith("stateDiagram-v2")


def test_fsm_effect_payload_schemas_match_reducer_outputs():
    spec = _load("fsm-spec.json")
    schemas = {
        name: set(effect["payload_schema"]["required"]) for name, effect in spec["effects"].items()
    }
    assert schemas == {
        "RECORD_TRANSITION": {
            "cycle_id",
            "event_intent_hash",
            "from_phase",
            "to_phase",
        },
        "AUDIT_REJECTION": {"code", "cycle_id", "event", "phase"},
        "RESTORE_REQUIRED": {"cycle_id", "reason"},
    }
    assert all(
        set(effect["payload_schema"]["properties"]) == set(effect["payload_schema"]["required"])
        and effect["payload_schema"]["additionalProperties"] is False
        for effect in spec["effects"].values()
    )

    snapshot = CycleSnapshot.start("cycle-contract", ProtocolBudget(20, 2))
    size = ProtocolEvent.create(
        "cycle-contract", "event-size", EventKind.SIZE, {"policy_version": "v1"}
    )
    accepted = step(snapshot, size)
    assert accepted.effects[0].kind is EffectKind.RECORD_TRANSITION
    assert set(accepted.effects[0].payload) == schemas["RECORD_TRANSITION"]

    invalid = ProtocolEvent.create("cycle-contract", "event-invalid", EventKind.GREEN, {})
    rejected = step(snapshot, invalid)
    assert rejected.effects[0].kind is EffectKind.AUDIT_REJECTION
    assert set(rejected.effects[0].payload) == schemas["AUDIT_REJECTION"]

    active = CycleSnapshot(
        identity=CycleIdentity("cycle-contract"),
        budget=ProtocolBudget(20, 2),
        phase=Phase.MUTATION_ACTIVE,
        mutation_active=True,
        mutation_delta=digest_raw(b"delta", scope="test-mutation", schema_version=PROTOCOL_VERSION),
        mutated_source=digest_raw(b"mutated", scope="test-source", schema_version=PROTOCOL_VERSION),
    )
    interrupt = ProtocolEvent.create(
        "cycle-contract", "event-interrupt", EventKind.INTERRUPT, {"reason": "cancelled"}
    )
    interrupted = step(active, interrupt)
    restore = next(
        effect for effect in interrupted.effects if effect.kind is EffectKind.RESTORE_REQUIRED
    )
    assert set(restore.payload) == schemas["RESTORE_REQUIRED"]


def test_fsm_liveness_claim_does_not_promise_caller_termination():
    spec = _load("fsm-spec.json")
    [claim] = spec["liveness_properties"]
    assert claim["id"] == "ordinary-progress-and-safety-tail-are-bounded"
    assert "does not guarantee caller termination" in claim["statement"]
    assert "invalid and replayed calls do not consume steps" in claim["verification"]
    assert "Caller-owned" in " ".join(claim["assumptions"])
    assert "fault overlay" in spec["scope"]
