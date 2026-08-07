"""Pure declarative gate-rule expansion and classification."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from .gate_primitives import _norm_op
from .gate_values import CheckFn, GatePolicy, JsonMap

_KEY_PROBES = (
    ("absent", "absent"),
    ("forbid", "absent"),
    ("heartbeat", "heartbeat"),
    ("must_order", "must_order"),
    ("present", "present"),
    ("ratioMetric", "ratioMetric"),
    ("conforms", "conforms"),
    ("invariant", "invariant"),
    ("metamorphic", "metamorphic"),
    ("duration", "duration"),
    ("external", "external"),
)

_STRENGTH_BY_KEY = MappingProxyType(
    {
        "absent": "forbid",
        "must_order": "ordered",
        "ratioMetric": "ratio",
        "heartbeat": "liveness",
        "conforms": "conformance",
        "invariant": "invariant",
        "metamorphic": "metamorphic",
        "external": "external",
        "duration": "threshold",
        "aggregate": "threshold",
    }
)

_STRENGTH_RANK = MappingProxyType(
    {
        "existence-only": 1,
        "bounded": 2,
        "threshold": 2,
        "value-pinned": 3,
        "ordered": 3,
        "forbid": 3,
        "ratio": 4,
        "liveness": 4,
        "conformance": 4,
        "invariant": 5,
        "metamorphic": 5,
        "external": 6,
    }
)
_COUNT_CONTROLS = frozenset(
    "event where indicatorRef op target count want optional pending weight strength "
    "label threshold events charged _auto".split()
)


def join_matchers(value: Any) -> str:
    items = value if isinstance(value, list) else [value]
    return ",".join(
        str(item.get("event") or item.get("where") or item) if isinstance(item, dict) else str(item)
        for item in items
    )


def label(check: Mapping[str, Any]) -> str:
    if "label" in check:
        return str(check["label"])
    if "external" in check:
        return "external:" + str(check.get("external"))
    if "metamorphic" in check:
        return "metamorphic:" + str(check.get("metamorphic"))
    if "invariant" in check:
        invariant = check["invariant"]
        return "invariant:" + (invariant if isinstance(invariant, str) else "expr")
    if "conforms" in check:
        return "conforms:" + str(check["conforms"])
    if "heartbeat" in check:
        return f"heartbeat:{check['heartbeat']}@{check.get('every_s')}s"
    if "must_order" in check:
        return "must_order:" + ">".join(check["must_order"])
    if "present" in check:
        return "present:" + join_matchers(check["present"])
    if "absent" in check:
        return "absent:" + join_matchers(check["absent"])
    if "ratio" in check:
        return f"ratio:{check['ratio']}{check.get('op', '')}{check.get('want', '')}"
    if check.get("event"):
        return str(check["event"])
    where = check.get("where") or {}
    if not where:
        return "(any)"
    return "where:" + ",".join(f"{key}={value}" for key, value in where.items())


def finite_gate_number(
    value: Any,
    label_text: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label_text} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{label_text} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{label_text} must be a finite number")
    if minimum is not None and number < minimum:
        raise ValueError(f"{label_text} must be >= {minimum:g}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{label_text} must be <= {maximum:g}")
    return number


def gate_threshold(value: Any) -> float:
    threshold = finite_gate_number(value, "gate threshold", minimum=0.0, maximum=1.0)
    if threshold <= 0.0:
        raise ValueError("gate threshold must be > 0")
    return threshold


def detect_check_key(rule: Mapping[str, Any], registry: Mapping[str, CheckFn]) -> str | None:
    for spec_key, canonical in _KEY_PROBES:
        if spec_key in rule:
            return canonical
    key = next((key for key in registry if key in rule), None)
    if key is None:
        validate_count_rule(rule)
    return key


def validate_count_rule(rule: Mapping[str, Any]) -> None:
    unknown = sorted(
        name
        for name, value in rule.items()
        if name not in _COUNT_CONTROLS and isinstance(value, (Mapping, list, tuple))
    )
    if unknown:
        raise ValueError(
            f"unknown gate predicate(s): {unknown!r}; structured predicates must "
            "register or import the extension that owns them"
        )


def strength(
    rule: Mapping[str, Any],
    registry: Mapping[str, CheckFn],
    strength_by_key: Mapping[str, str],
) -> str:
    declared = rule.get("strength")
    if isinstance(declared, str) and declared:
        return declared
    key = detect_check_key(rule, registry)
    if key is not None and key in strength_by_key:
        return strength_by_key[key]
    if key == "present":
        matchers = rule.get("present") or []
        return "value-pinned" if any(item.get("where") for item in matchers) else "existence-only"
    if rule.get("where"):
        return "value-pinned"
    if rule.get("threshold") is not None:
        return "threshold"
    tight = _norm_op(rule.get("op", ">=")) in ("==", "!=", "<=", "<")
    return "bounded" if tight else "existence-only"


def rule_event_names(
    rule: Mapping[str, Any], registry: Mapping[str, CheckFn] | None = None
) -> set[str]:
    names: set[str] = set()
    _add_matcher_event_names(rule, names)
    _add_ordered_event_names(rule, names)
    _add_nested_event_names(rule, names)
    resolver = _event_name_resolver(rule, registry)
    if callable(resolver):
        for value in resolver(rule):
            _add_event_name(names, value)
    return names


def _add_event_name(names: set[str], value: Any) -> None:
    if isinstance(value, str) and value:
        names.add(value)


def _add_matcher_event_names(rule: Mapping[str, Any], names: set[str]) -> None:
    for key in ("present", "absent", "forbid"):
        value = rule.get(key)
        matchers = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
        for matcher in matchers:
            if isinstance(matcher, dict):
                _add_event_name(names, matcher.get("event"))


def _add_ordered_event_names(rule: Mapping[str, Any], names: set[str]) -> None:
    for part in rule.get("must_order") or []:
        event = (
            part if isinstance(part, str) else part.get("event") if isinstance(part, dict) else None
        )
        _add_event_name(names, event)


def _event_name_resolver(rule: Mapping[str, Any], registry: Mapping[str, CheckFn] | None) -> Any:
    if registry is None:
        return None
    predicate_key = detect_check_key(rule, registry)
    handler = registry.get(predicate_key) if predicate_key is not None else None
    return getattr(handler, "__ooptdd_event_names__", None)


def _add_nested_event_names(rule: Mapping[str, Any], names: set[str]) -> None:
    for container, sides in (
        ("ratioMetric", ("good", "total")),
        ("invariant", ("left", "right")),
        ("metamorphic", ("a", "b")),
    ):
        value = rule.get(container)
        if isinstance(value, dict):
            for side in sides:
                nested = value.get(side)
                if isinstance(nested, dict):
                    _add_event_name(names, nested.get("event"))
    for key in ("duration", "aggregate"):
        value = rule.get(key)
        if isinstance(value, dict):
            _add_event_name(names, value.get("event"))
    _add_event_name(names, rule.get("heartbeat"))
    _add_event_name(names, rule.get("conforms") if isinstance(rule.get("conforms"), str) else None)
    _add_event_name(names, rule.get("event"))
    for value in rule.get("events") or []:
        _add_event_name(names, value)


def check_charged(check: Mapping[str, Any]) -> bool:
    if "charged" in check:
        return bool(check["charged"])
    if "got" in check:
        return bool(check["got"] > 0)
    if "present" in check:
        return len(check.get("missing", [])) < len(check.get("present", []))
    if "must_order" in check:
        return any(value is not None for value in check.get("firsts", {}).values())
    if "ratio" in check:
        return bool(check.get("total", 0) > 0)
    if "invariant" in check:
        return check.get("reason") != "invariant_no_evidence"
    if "metamorphic" in check:
        return check.get("reason") != "metamorphic_no_evidence"
    if "external" in check:
        return check.get("probe_reachable") is True and check.get("value") is not None
    if "heartbeat" in check:
        return bool(check.get("beats", 0) > 0)
    if "conforms" in check:
        return bool(check.get("checked", 0) > 0 or check.get("unknown"))
    if "absent" in check:
        return bool(check.get("violations", 0) > 0)
    return False


def validate_allow_errors(spec: Mapping[str, Any]) -> None:
    for allowed in spec.get("allow_errors") or []:
        if not isinstance(allowed, dict) or not (allowed.get("event") or allowed.get("where")):
            raise ValueError(
                f"allow_errors entry {allowed!r} matches every event (no event/where) — it would "
                "disable the entire negative wing; name the benign error explicitly"
            )


def expand_rules(spec: Mapping[str, Any], policy: GatePolicy) -> list[JsonMap]:
    validate_allow_errors(spec)
    rules = copy.deepcopy(list(spec.get("expect", [])))
    if policy.forbid_errors:
        levels = spec.get("error_levels") or ["ERROR", "CRITICAL"]
        rules.append(
            {"absent": [{"where": {"level": level}} for level in levels], "_auto": "forbid_errors"}
        )
    pinned_service = spec.get("pin_service")
    if pinned_service:
        rules.append(
            {
                "invariant": {
                    "left": {"reduce": "count"},
                    "right": {"where": {"service": pinned_service}, "reduce": "count"},
                    "op": "==",
                },
                "label": f"pin_service={pinned_service}",
                "_auto": "pin_service",
            }
        )
    return rules


__all__ = (
    "_KEY_PROBES",
    "_STRENGTH_BY_KEY",
    "_STRENGTH_RANK",
    "check_charged",
    "detect_check_key",
    "expand_rules",
    "finite_gate_number",
    "gate_threshold",
    "join_matchers",
    "label",
    "rule_event_names",
    "strength",
    "validate_count_rule",
)
