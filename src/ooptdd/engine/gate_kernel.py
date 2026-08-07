"""Referentially transparent gate judgement.

This module is the functional core of the gate engine.  It receives every source of
variation as a value: policy, readback identity, predicate registry, strength registry,
and already-observed external facts.  It does not read environment variables, query a
backend, call a probe, or mutate caller-owned values.

``ooptdd.engine.gate`` is the imperative compatibility shell.  It resolves ambient
configuration and ports, snapshots them into :class:`GateEvaluation`, and thaws the
immutable :class:`GateVerdict` for the historical dictionary API.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from typing import Any

from ..domain.model import verify_chain
from .gate_primitives import stream_key
from .gate_rules import (
    check_charged,
    detect_check_key,
    expand_rules,
    finite_gate_number,
    gate_threshold,
    label,
    rule_event_names,
    strength,
)
from .gate_values import (
    CheckCtx,
    CheckFn,
    ExternalObservation,
    GateEvaluation,
    GatePolicy,
    GateSource,
    GateVerdict,
    JsonMap,
    ReplayProbe,
    freeze_value,
    thaw_value,
)


def _decorate_check(
    raw: JsonMap,
    rule: JsonMap,
    *,
    key: str | None,
    handler: CheckFn,
    source: GateSource,
    registry: Mapping[str, CheckFn],
    strength_by_key: Mapping[str, str],
) -> JsonMap:
    if "passed" not in raw or type(raw["passed"]) is not bool:
        name = getattr(handler, "__name__", repr(handler))
        raise ValueError(
            f"check handler {name!r} (key={key!r}) must return a dict with an exact bool "
            f"'passed' value. Got: {raw!r}"
        )
    check = dict(raw)
    check["optional"] = bool(rule.get("optional", False))
    check["pending"] = bool(rule.get("pending", False))
    check["weight"] = finite_gate_number(rule.get("weight", 1.0), "gate check weight", minimum=0.0)
    check["strength"] = strength(rule, registry, strength_by_key)
    check["kind"] = key or "count"
    if "label" in rule and "label" not in check:
        check["label"] = rule["label"]
    check["label"] = label(check)
    _decorate_grounding(check, source)
    check["charged"] = check_charged(check)
    return check


def _decorate_grounding(check: JsonMap, source: GateSource) -> None:
    derived = check.get("derived_identity")
    same_endpoint = (
        check["strength"] == "external"
        and derived is not None
        and source.emit_identity is not None
        and str(derived).rstrip("/") == source.emit_identity.rstrip("/")
    )
    if same_endpoint and check.get("separate_source"):
        check["demoted_same_endpoint"] = True
    effective_separate = bool(check.get("separate_source")) and not same_endpoint
    check["grounding"] = (
        "corroborated" if check["strength"] == "external" and effective_separate else "derived-self"
    )


def evaluate_checks(
    evaluation: GateEvaluation,
    spec: JsonMap,
    events: list[JsonMap],
    rules: list[JsonMap],
) -> list[JsonMap]:
    checks: list[JsonMap] = []
    indicators = freeze_value(spec.get("indicators") or {})
    allow_errors = tuple(freeze_value(value) for value in spec.get("allow_errors") or [])
    for index, rule in enumerate(rules):
        observation = evaluation.external_observations.get(index)
        context = CheckCtx(
            reachable=evaluation.source.reachable and evaluation.source.complete,
            indicators=indicators,
            ontology=evaluation.ontology,
            allow_errors=allow_errors,
            external_observation=observation,
            probe=ReplayProbe(observation) if observation is not None else None,
            cid=evaluation.source.cid,
        )
        key = detect_check_key(rule, evaluation.registry)
        handler = evaluation.registry.get(key or "__count__")
        if handler is None:
            raise ValueError(f"no check handler registered for {key or '__count__'!r}")
        handler_events = [thaw_value(freeze_value(event)) for event in events]
        handler_rule = thaw_value(freeze_value(rule))
        captured_result = evaluation.captured_check_results.get(index)
        raw = (
            thaw_value(captured_result)
            if captured_result is not None
            else handler(handler_events, handler_rule, context)
        )
        if not isinstance(raw, dict):
            name = getattr(handler, "__name__", repr(handler))
            raise ValueError(
                f"check handler {name!r} (key={key!r}) must return a dict with an exact bool "
                f"'passed' value. Got: {raw!r}"
            )
        captured_raw = thaw_value(freeze_value(raw))
        checks.append(
            _decorate_check(
                captured_raw,
                rule,
                key=key,
                handler=handler,
                source=evaluation.source,
                registry=evaluation.registry,
                strength_by_key=evaluation.strength_by_key,
            )
        )
    return checks


def _required_outcome(
    checks: list[JsonMap], threshold: Any
) -> tuple[bool, float | None, float | None]:
    if threshold is None:
        return all(bool(check["passed"]) for check in checks), None, None
    threshold_value = gate_threshold(threshold)
    total = sum(float(check["weight"]) for check in checks)
    if not math.isfinite(total) or total <= 0.0:
        raise ValueError("weighted gate requires a finite positive total gating weight")
    passed = sum(float(check["weight"]) for check in checks if check["passed"])
    if not math.isfinite(passed):
        raise ValueError("weighted gate passed-weight total must be finite")
    score = passed / total
    return score >= threshold_value, score, threshold_value


def _authentication(
    policy: GatePolicy, events: list[JsonMap], asserts_anything: bool
) -> bool | None:
    if not policy.require_signature or not asserts_anything:
        return None
    if not policy.signing_key:
        return False
    chain = [
        {key: value for key, value in event.items() if not key.startswith("_")} for event in events
    ]
    return bool(verify_chain(chain, policy.signing_key)["ok"])


def _scope(
    checks: list[JsonMap],
    gating: list[JsonMap],
    events: list[JsonMap],
    rules: list[JsonMap],
    registry: Mapping[str, CheckFn],
) -> JsonMap:
    observed = {
        event_name
        for event in events
        if isinstance((event_name := event.get("event")), str) and event_name
    }
    asserted = (
        set().union(*(rule_event_names(rule, registry) for rule in rules)) if rules else set()
    )
    named = observed & asserted
    charged = sum(1 for check in gating if check.get("charged"))
    return {
        "gating": len(gating),
        "optional": sum(1 for check in checks if check["optional"]),
        "pending": sum(1 for check in checks if check["pending"]),
        "total": len(checks),
        "asserts_anything": bool(gating),
        "by_strength": dict(Counter(check["strength"] for check in gating)),
        "observed_event_types": len(observed),
        "named_event_types": len(named),
        "unasserted_observed": sorted(observed - asserted)[:10],
        "stream_coverage": (len(named) / len(observed)) if observed else None,
        "charged": charged,
        "charge_ratio": (charged / len(gating)) if gating else None,
        "uncharged": [label(check) for check in gating if not check.get("charged")][:10],
    }


def _oracle(
    evaluation: GateEvaluation,
    gating: list[JsonMap],
    corroborated: int,
) -> JsonMap:
    source = evaluation.source
    policy = evaluation.policy
    return {
        "gating": len(gating),
        "corroborated": corroborated,
        "derived_self": len(gating) - corroborated,
        "single_authority": bool(gating) and corroborated == 0,
        "enforced": policy.require_corroboration,
        "emit_backend": source.emit_backend,
        "emit_identity": source.emit_identity,
        "emit_independent": source.emit_independent,
        "independent_store_enforced": policy.require_independent_store,
        "relocated": sum(1 for check in gating if check.get("demoted_same_endpoint")),
        "signature_enforced": policy.require_signature,
        "forbid_errors": policy.forbid_errors,
    }


def _build_result(
    evaluation: GateEvaluation,
    checks: list[JsonMap],
    rules: list[JsonMap],
) -> JsonMap:
    gating = [
        check
        for check in checks
        if not check["optional"] and not check["pending"] and not check.get("tautological")
    ]
    asserts_anything = bool(gating)
    required_ok, score, threshold = _required_outcome(
        gating, thaw_value(evaluation.spec).get("threshold")
    )
    corroborated = sum(
        1 for check in gating if check.get("grounding") == "corroborated" and check.get("passed")
    )
    thawed_events = [thaw_value(event) for event in evaluation.events]
    authenticated = _authentication(evaluation.policy, thawed_events, asserts_anything)
    source, policy = evaluation.source, evaluation.policy
    uncorroborated = policy.require_corroboration and asserts_anything and corroborated == 0
    unauthenticated = policy.require_signature and asserts_anything and authenticated is not True
    dependent_store = (
        policy.require_independent_store
        and asserts_anything
        and source.emit_independent is False
        and corroborated == 0
    )
    result: JsonMap = {
        "ok": source.reachable
        and source.complete
        and asserts_anything
        and required_ok
        and not uncorroborated
        and not unauthenticated
        and not dependent_store,
        "reachable": source.reachable,
        "complete": source.complete,
        "probe_reachable": not any(check.get("probe_reachable") is False for check in checks),
        "vacuous": bool(checks) and not asserts_anything,
        "uncorroborated": uncorroborated,
        "unauthenticated": unauthenticated,
        "dependent_store": dependent_store,
        "authenticated": authenticated,
        "cid": source.cid,
        "checks": checks,
        "oracle": _oracle(evaluation, gating, corroborated),
        "scope": _scope(checks, gating, thawed_events, rules, evaluation.registry),
        "optional_failed": [
            label(check) for check in checks if check["optional"] and not check["passed"]
        ],
        "pending_failed": [
            label(check) for check in checks if check["pending"] and not check["passed"]
        ],
        "pending_satisfied": [
            label(check) for check in checks if check["pending"] and check["passed"]
        ],
    }
    if score is not None:
        result["score"] = score
        result["threshold"] = threshold
    if source.emit_sampled:
        result["sampled"] = True
    return result


def judge_events(evaluation: GateEvaluation) -> GateVerdict:
    """Judge closed values without ambient effects.

    Injected check handlers are isolated from one another and must obey ``CheckFn``'s
    deterministic-function contract; Python cannot mechanically prevent a caller-provided
    callable from performing effects outside this module.
    """

    spec = thaw_value(evaluation.spec)
    events = sorted((thaw_value(event) for event in evaluation.events), key=stream_key)
    rules = expand_rules(spec, evaluation.policy)
    checks = evaluate_checks(evaluation, spec, events, rules)
    return GateVerdict.capture(_build_result(evaluation, checks, rules))


__all__ = (
    "CheckCtx",
    "CheckFn",
    "ExternalObservation",
    "GateEvaluation",
    "GatePolicy",
    "GateSource",
    "GateVerdict",
    "judge_events",
)
