"""Gate runner — evaluate a YAML trace spec against a backend.

A gate is plain data describing what a backend must report. It is intentionally
count-based: existence and cardinality remain robust on eventually-consistent stores.

Spec format (``gates/*.yaml``)::

    cid_env: OOPTDD_CID        # or:  cid: a-literal-correlation-id
    service: myapp             # optional, informational
    timeWindow: 1h             # optional rolling readback window (OpenSLO style:
                               #   30s/5m/2h/1d or bare seconds); default = backend's
    indicators:                # optional SLI layer — *how to select* (named, reusable)
      errors:    {event: request.completed, where: {status: error}}
      completed: {event: request.completed, where: {status: success}}
    expect:                    # the SLO layer — *what counts as green* (criteria)
      - event: batch_completed
        op: ">="              # symbolic (>= > == <= <) OR OpenSLO words (gte/gt/eq/lte/lt)
        count: 1
      - event: item_processed
        op: gte                #   `target:` is an alias for `count:`
        target: 5
      - indicatorRef: errors    # reuse a named indicator; criteria stay here
        op: eq
        target: 0
      - ratioMetric:           # good/total ratio (OpenSLO ratioMetric)
          good:  {indicatorRef: completed}
          total: {event: request.completed}
        op: gte
        target: 0.99
      - present:               # subset-present, ANY order;
          - {event: a}         #   each matcher must match >=1 event. The default "did these
          - {event: b, where: {station: A}}   #   happen?" check — order is NOT asserted.
      - must_order: [a, b, c]  # each must occur, first-occurrence times non-decreasing
      - absent:                # the negative wing — matching events must NOT occur (count 0).
          where: {level: ERROR}   #   the mirror of `present`. Offenders are surfaced so an
                               #   error log becomes a hard failure, not green-and-noisy.
      - event: optional_stream # optional: a threshold miss does NOT fail the gate,
        op: ">="               #   but it IS surfaced (and an unreachable store is still
        count: 1               #   INFRA, reported via `reachable`, never a clean pass)
        optional: true
      - aggregate:             # numeric rollup budget (sum/max/min/avg of an attr)
          {fn: sum, attr: duration_ms, target: 50000}
    forbid_errors: true        # optional (spec-level): inject an implicit ERROR/CRITICAL
                               #   `absent` into the gate (caller policy may map environment;
                               #   set false here to opt a spec out). Levels via `error_levels:`.
    allow_errors:              # optional (spec-level) allowlist — these matched errors are
      - {event: zdf.drop}      #   exempt (known-benign), so they don't flip the gate.

Counting is done over the events the backend returns for ``cid`` — no
backend-specific query language, so the same gate runs on memory, OpenObserve, or
any future driver. ``where`` filters on arbitrary event fields (e.g. ``verdict``,
``level``) by partial-dict equality — only the listed keys must match.
``must_order`` checks sequencing
using each event's ``_timestamp`` (store-receive time) — so an ordering verdict is only as
trustworthy as the transport's order-preservation. Prefer ``invariant`` or ``external:``
when the transport can reorder and ordering itself is being verified. ``present`` asserts a
subset occurred in any order.

The vocabulary (``op: gte``, ``target``, ``timeWindow``, ``indicators``/``indicatorRef``,
``ratioMetric``) is deliberately aligned with **OpenSLO** and **Keptn** SLO specs so
a gate reads like an SLO objective and the SLI ("how to query") is decoupled from the
SLO ("what is green") and reusable. Symbolic operators and ``count`` remain first-class —
the alignment is additive, the evaluation logic is unchanged.

Evaluation is **streaming**: each check compiles to an LTL₃/MTL monitor automaton
(:mod:`ooptdd.monitor`) that is fed the event prefix in store-timestamp order and reports
a three-valued verdict (``sat``/``viol``/``pend``) plus the index at which it settled. The
final collapsed pass/fail is identical to the historical count comparison; what the gate
gains is a real incremental monitor with anticipatory verdicts, surfaced per check as
``verdict``/``settled_at``.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from typing import Any

from ..domain.ports import (
    Backend,
    Clock,
    ExternalProbe,
    QuerySpec,
    SystemClock,
    TimeWindow,
    backend_caps,
    backend_identity,
    fetch,
)
from ..domain.settings import (
    DEFAULT_ENV_KEYS,
    FALSE_VALUES,
    TRUE_VALUES,
    EnvironmentKeys,
)
from .gate_kernel import judge_events
from .gate_primitives import stream_key
from .gate_rules import (
    _KEY_PROBES,  # noqa: F401 - compatibility re-export
    _STRENGTH_BY_KEY,
    _STRENGTH_RANK,
    expand_rules,
)
from .gate_rules import (
    check_charged as _kernel_check_charged,
)
from .gate_rules import (
    detect_check_key as _kernel_detect_check_key,
)
from .gate_rules import (
    finite_gate_number as _kernel_finite_gate_number,
)
from .gate_rules import (
    gate_threshold as _kernel_gate_threshold,
)
from .gate_rules import (
    label as _kernel_label,
)
from .gate_rules import (
    rule_event_names as _kernel_rule_event_names,
)
from .gate_rules import (
    strength as _kernel_strength,
)
from .gate_values import (
    CheckCtx,
    CheckFn,
    ExternalObservation,
    GateEvaluation,
    GatePolicy,
    GateSource,
    freeze_value,
    thaw_value,
)
from .monitor import (  # the evaluation kernel
    _OPS,
    _matches,
    _norm_op,
    _resolve_matcher,
    compile_check,
    run_monitor,
)


def matches_event(event: dict[str, Any], event_name: str | None, where: dict[str, Any]) -> bool:
    """Return whether an event satisfies a generic event/attribute matcher."""

    return _matches(event, event_name, where)


def resolve_matcher(
    matcher: dict[str, Any],
    indicators: dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    """Resolve an inline matcher or an indicator reference into event and attributes."""

    return _resolve_matcher(matcher, indicators)


# ---- immutable check-predicate registry (the extension seam) ---------------- #


class CheckRegistry(Mapping[str, CheckFn]):
    """Immutable predicate mapping captured at a composition boundary."""

    def __init__(self, handlers: Mapping[str, CheckFn]) -> None:
        self._handlers = MappingProxyType(dict(handlers))

    def __getitem__(self, key: str) -> CheckFn:
        return self._handlers[key]

    def __iter__(self):
        return iter(self._handlers)

    def __len__(self) -> int:
        return len(self._handlers)

    def snapshot(self, *, default: CheckFn | None = None) -> Mapping[str, CheckFn]:
        values = dict(self._handlers)
        if default is not None:
            values["__count__"] = default
        return MappingProxyType(values)

    def strength_snapshot(self) -> Mapping[str, str]:
        """Return strength metadata declared by registered handlers."""
        return MappingProxyType(
            {
                key: value
                for key, handler in self._handlers.items()
                if isinstance((value := getattr(handler, "__ooptdd_strength__", None)), str)
                and value
            }
        )


def check_strengths(
    registry: Mapping[str, CheckFn] | None = None,
) -> Mapping[str, str]:
    """Snapshot built-in and explicitly registered predicate strength metadata."""
    handlers = core_check_registry() if registry is None else registry
    strengths = dict(_STRENGTH_BY_KEY)
    strengths.update(
        {
            key: value
            for key, handler in handlers.items()
            if isinstance((value := getattr(handler, "__ooptdd_strength__", None)), str) and value
        }
    )
    return strengths


def check(
    *keys: str,
    strength: str | None = None,
    event_names: Callable[[Mapping[str, Any]], object] | None = None,
) -> Callable[[CheckFn], CheckFn]:
    """Attach predicate metadata without registering or mutating process state."""

    if strength is not None and (not isinstance(strength, str) or not strength):
        raise ValueError("check strength metadata must be non-empty text")
    if event_names is not None and not callable(event_names):
        raise TypeError("check event_names metadata must be callable")

    if not keys or any(not isinstance(key, str) or not key for key in keys):
        raise ValueError("check predicate keys must be non-empty text")
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate check predicate key in decorator")

    def deco(fn: CheckFn) -> CheckFn:
        metadata_handler: Any = fn
        metadata_handler.__ooptdd_check_keys__ = tuple(keys)
        if strength is not None:
            metadata_handler.__ooptdd_strength__ = strength
        if event_names is not None:
            metadata_handler.__ooptdd_event_names__ = event_names
        return fn

    return deco


def checks_from(*handlers: CheckFn) -> Mapping[str, CheckFn]:
    """Create an immutable provider mapping from metadata-only decorated handlers."""
    result: dict[str, CheckFn] = {}
    for handler in handlers:
        keys = getattr(handler, "__ooptdd_check_keys__", ())
        if not keys:
            raise ValueError(f"check handler {handler!r} has no @check metadata")
        for key in keys:
            if key in result:
                raise ValueError(f"duplicate check predicate {key!r}")
            result[key] = handler
    return MappingProxyType(result)


def compose_check_registry(
    *providers: Mapping[str, CheckFn],
    base: Mapping[str, CheckFn] | None = None,
) -> CheckRegistry:
    """Compose immutable predicate mappings, rejecting ambiguous ownership."""
    combined = dict(core_check_registry() if base is None else base)
    for provider in providers:
        for key, handler in provider.items():
            if not isinstance(key, str) or not key or not callable(handler):
                raise TypeError("check registries require non-empty string keys and callables")
            if key in combined:
                raise ValueError(f"duplicate check predicate {key!r}")
            combined[key] = handler
    return CheckRegistry(combined)


_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def duration_s(v) -> int | None:
    """Parse an OpenSLO-style rolling window: ``30s`` / ``5m`` / ``2h`` / ``1d`` /
    bare seconds (int or numeric string). ``None`` -> ``None`` (use backend default)."""
    if v is None:
        return None
    if isinstance(v, bool):  # guard: bool is an int subclass
        raise ValueError(f"invalid timeWindow: {v!r}")
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().lower()
    if s and s[-1] in _UNITS and s[:-1].isdigit():
        return int(s[:-1]) * _UNITS[s[-1]]
    return int(s)  # bare numeric string -> seconds


def load_gate(path: str, *, cid: str | None = None) -> dict:
    import yaml  # PyYAML (declared dependency)

    # UTF-8 explicitly — a gate spec is YAML, and YAML is UTF-8 by specification.
    # Reading it through the locale codec breaks non-ASCII specs on systems
    # whose locale encoding is not UTF-8.
    with open(path, encoding="utf-8") as fh:
        try:
            spec = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            # YAMLError is NOT a ValueError, so it would escape the CLI's clean-error handler as an
            # uncaught traceback (exit 1) — re-raise as ValueError so a malformed spec is exit 2.
            raise ValueError(f"malformed gate spec {path}: {exc}") from exc
    if cid is not None:
        spec["cid"] = cid  # explicit override of the file's cid/cid_env (no monkeypatch needed)
    return spec


def _join_matchers(v) -> str:
    """Label helper total over BOTH shapes `_label` is fed: a RESULT's list of names
    (strings) and a RULE's list of matcher dicts — `",".join` on the raw value raised
    TypeError from `ooptdd lint` on any legitimate `present:[{event: a}, ...]` spec."""
    from .gate_rules import join_matchers

    return join_matchers(v)


def _label(chk: dict) -> str:
    """Human handle for a check (used to surface optional failures)."""
    return _kernel_label(chk)


def _truthy(v) -> bool:
    return str(v).strip().lower() in TRUE_VALUES if v is not None else False


def _finite_gate_number(
    value,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Parse a gate scalar without accepting booleans, infinities, or unsafe ranges."""
    return _kernel_finite_gate_number(value, label, minimum=minimum, maximum=maximum)


def _exact_external_number(value: Any, label: str) -> Fraction:
    """Preserve exact numeric identity for external-oracle comparisons."""

    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        return Fraction(value)
    except (TypeError, ValueError, OverflowError, ZeroDivisionError) as error:
        raise ValueError(f"{label} must be a finite number") from error


def _compare_exact_external(op: str, observed: Fraction, expected: Fraction) -> bool:
    if op == ">=":
        return observed >= expected
    if op == ">":
        return observed > expected
    if op == "<=":
        return observed <= expected
    if op == "<":
        return observed < expected
    if op == "==":
        return observed == expected
    raise ValueError(f"unknown external comparison operator {op!r}")


def _gate_threshold(value) -> float:
    return _kernel_gate_threshold(value)


# ---- registered check handlers (thin adapters over the kernel's compile_check) ---- #
# Every built-in handler compiles its rule to the right Monitor via the kernel's single
# source of truth (compile_check) and drives it over the (already stream-ordered) events.
# The batch path here and the live path (monitor.LiveMonitorSet) therefore share one
# rule->automaton compiler and can never diverge. Custom @check predicates (user-registered)
# remain free to return their own dicts — they are a gate-layer seam, not kernel monitors.


def _run(rule: dict, events: list, ctx: CheckCtx) -> dict:
    # allow_errors is scoped to the AUTO-injected forbid_errors wing ONLY (grill F2b): it is
    # the known-benign allowlist for the implicit ERROR/CRITICAL absent, and must NOT bleed
    # into a USER-authored `absent:` check — a user who forbids `zdf.drop@B` means it, and the
    # spec-level allowlist (intended for the error wing) silently exempting it is a fail-open.
    allow = list(ctx.allow_errors) if rule.get("_auto") == "forbid_errors" else None
    monitor = compile_check(
        rule, indicators=dict(ctx.indicators), ontology=ctx.ontology, allow=allow
    )
    return run_monitor(monitor, events, ctx.reachable)


@check("absent")
def _check_absent(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("heartbeat")
def _check_heartbeat(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("must_order")
def _check_must_order(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("present")
def _check_present(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("ratioMetric")
def _check_ratio(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("conforms")
def _check_conforms(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("invariant")
def _check_invariant(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)


@check("metamorphic")
def _check_metamorphic(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)  # within-run: pure, two matched subsets of the one stream


@check("duration")
def _check_duration(events: list, rule: dict, ctx: CheckCtx) -> dict:
    return _run(rule, events, ctx)  # universal field threshold (kernel DurationMonitor)


_AGGREGATES: Mapping[str, Callable[[list[float]], float]] = MappingProxyType(
    {
        "sum": sum,
        "max": max,
        "min": min,
        "avg": lambda values: sum(values) / len(values),
    }
)


@check("aggregate", strength="threshold")
def _check_aggregate(events: list, rule: dict, ctx: CheckCtx) -> dict:
    """Apply a numeric rollup to one field across matching structured events."""

    spec = rule["aggregate"]
    if not isinstance(spec, dict):
        raise ValueError("aggregate requires a dict: {fn, attr, target, [event, op]}")
    fn = spec.get("fn", "sum")
    if fn not in _AGGREGATES:
        raise ValueError(f"aggregate fn must be one of {sorted(_AGGREGATES)}: {fn!r}")
    if "attr" not in spec or "target" not in spec:
        raise ValueError("aggregate requires `attr:` and `target:`")
    attr = spec["attr"]
    event_name = spec.get("event")
    values: list[float] = []
    for event in events:
        if event_name is not None and event.get("event") != event_name:
            continue
        value = event.get(attr)
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if math.isfinite(value):
            values.append(value)
    op = _norm_op(spec.get("op", "<="))
    if op not in _OPS:
        raise ValueError(f"unknown op {spec.get('op', '<=')!r}; one of {sorted(_OPS)}")
    target = _finite_gate_number(spec["target"], "aggregate target")
    base = {
        "label": f"aggregate:{fn}({attr})",
        "aggregate": fn,
        "attr": attr,
        "op": op,
        "target": target,
        "n": len(values),
        "charged": bool(values),
    }
    if not values:
        value = 0.0 if fn == "sum" else None
        passed = bool(_OPS[op](0.0, target)) if fn == "sum" else False
        return {**base, "value": value, "passed": passed, "reason": "aggregate_no_values"}
    value = _AGGREGATES[fn](values)
    return {**base, "value": value, "passed": bool(_OPS[op](value, target))}


@check("external")
def _check_external(events: list, rule: dict, ctx: CheckCtx) -> dict:
    """The independent-oracle check: assert against an external fact (ctx.probe), NOT the system's
    own emit. A missing probe is a loud misconfiguration (never a silent green); an unreachable
    probe is inconclusive (surfaced via ``probe_reachable=False``, never a strict fail)."""
    spec = rule["external"]
    op = _norm_op(spec.get("op", "=="))
    base = {
        "external": spec.get("kind", "?"),
        "op": op,
        "want": spec.get("want"),
        "selector": spec.get("selector"),
    }
    observation = ctx.external_observation
    if observation is None:
        return {
            **base,
            "passed": False,
            "probe_reachable": None,
            "reason": "no_external_probe_configured",
        }
    if not observation.reachable or not observation.complete:
        return {
            **base,
            "passed": False,
            "probe_reachable": False,
            "value": None,
            "reason": "external_probe_unreachable",
        }
    # ExternalObservation stores a deep immutable snapshot.  Compare and expose a
    # fresh compatibility value so representation details such as tuple-backed
    # captured lists cannot change the rule's semantics.
    value, want = thaw_value(observation.value), spec.get("want")
    if want is None:
        passed = value is not None  # the external fact merely has to EXIST
    elif op in ("==", "!="):
        tolerance = _exact_external_number(spec.get("tol", 0.0), "external tolerance")
        if tolerance < 0:
            raise ValueError("external tolerance must be >= 0")
        numeric_pair = all(
            isinstance(item, int | float | Decimal | Fraction) and not isinstance(item, bool)
            for item in (value, want)
        )
        if op == "!=":
            passed = value != want
        elif numeric_pair:
            observed_number = _exact_external_number(value, "external observed value")
            expected_number = _exact_external_number(want, "external expected value")
            passed = abs(observed_number - expected_number) <= tolerance
        else:
            passed = value == want
    else:
        if op not in _OPS:
            raise ValueError(f"unknown external comparison operator {op!r}")
        observed_number = _exact_external_number(value, "external observed value")
        expected_number = _exact_external_number(want, "external expected value")
        passed = _compare_exact_external(op, observed_number, expected_number)
    return {
        **base,
        "value": value,
        "probe_reachable": True,
        "separate_source": observation.separate_source,
        "derived_identity": observation.derived_identity,
        "passed": bool(passed),
    }


_CORE_CHECK_REGISTRY = CheckRegistry(
    {
        "absent": _check_absent,
        "heartbeat": _check_heartbeat,
        "must_order": _check_must_order,
        "present": _check_present,
        "ratioMetric": _check_ratio,
        "conforms": _check_conforms,
        "invariant": _check_invariant,
        "metamorphic": _check_metamorphic,
        "duration": _check_duration,
        "aggregate": _check_aggregate,
        "external": _check_external,
    }
)

# Compatibility name retained as an immutable view. It cannot be registered into,
# cleared, or otherwise changed by imports or tests.
CHECK_REGISTRY = _CORE_CHECK_REGISTRY


def core_check_registry() -> Mapping[str, CheckFn]:
    """Return the immutable built-in registry, independent of compatibility mutations."""

    return _CORE_CHECK_REGISTRY


def _eval_count(events: list, rule: dict, ctx: CheckCtx) -> dict:
    """The default check (no predicate keyword): a :class:`CountMonitor` over the rule's
    event/where compared with op/target. The documented fallback when no registered
    predicate key is present."""
    return _run(rule, events, ctx)


_CANONICAL_GATE_HANDLERS = frozenset(
    {
        _check_absent,
        _check_heartbeat,
        _check_must_order,
        _check_present,
        _check_ratio,
        _check_conforms,
        _check_invariant,
        _check_metamorphic,
        _check_duration,
        _check_aggregate,
        _check_external,
        _eval_count,
    }
)


def _detect_check_key(
    rule: dict,
    registry: Mapping[str, CheckFn] | None = None,
) -> str | None:
    """The registry key for ``rule`` (``None`` -> the default count check). Built-in keys
    win in historical order; an externally-registered custom key is matched after."""
    handlers = core_check_registry() if registry is None else registry
    return _kernel_detect_check_key(rule, handlers)


# ---- strength / scope signal (honesty, not an oracle) ----------------------- #
# A GREEN gate must not be misread as "the system is correct": it only proves the events the
# author NAMED arrived with the asserted shape. `_strength` classifies a check's discriminating
# power (from the rule alone), so a gate can self-report HOW HARD it asserted — an all
# `existence-only` gate proved tokens were emitted, pinned no field, ordered nothing, forbade
# nothing. Higher strength is still author-vs-author (the `where` value descends from the same
# mental model) — a harder self-check, not an external oracle.
def _strength(
    rule: dict,
    registry: Mapping[str, CheckFn] | None = None,
    strength_by_key: Mapping[str, str] | None = None,
) -> str:
    """Discriminating-power class of a check (pure, total over every registry key + the default
    count). Low→high: existence-only < bounded < value-pinned/ordered/forbid/threshold <
    ratio/liveness/conformance."""
    handlers = core_check_registry() if registry is None else registry
    strengths = dict(check_strengths(handlers))
    if strength_by_key is not None:
        strengths.update(strength_by_key)
    return _kernel_strength(rule, handlers, strengths)


def _rule_event_names(
    rule: dict,
    registry: Mapping[str, CheckFn] | None = None,
) -> set[str]:
    """Event names a single gate rule asserts on, best-effort across every check shape — used to
    measure how much of the OBSERVED stream the gate actually names (the closed-world signal)."""
    handlers = core_check_registry() if registry is None else registry
    return _kernel_rule_event_names(rule, handlers)


def _check_charged(chk: dict) -> bool:
    """Did a check actually SEE matching evidence (positive confirmation), vs pass on absence /
    emptiness? The charge-ratio over gating checks measures how much of a green is backed by
    observed events rather than by nothing happening — distinct from stream-coverage."""
    return _kernel_check_charged(chk)


def _resolve_cid(
    spec: Mapping[str, object],
    environ: Mapping[str, str] | None = None,
    *,
    env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
) -> str:
    if spec.get("cid"):
        return str(spec["cid"])
    env = str(spec.get("cid_env", env_keys.cid))
    cid = ({} if environ is None else environ).get(env)
    if not cid:
        raise ValueError(f"gate needs a cid: set `cid:` in the spec or export {env}")
    return cid


def resolve_gate_policy(
    spec: Mapping[str, object],
    environ: Mapping[str, str] | None = None,
    *,
    env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
) -> GatePolicy:
    """Purely resolve spec policy over an explicit environment snapshot.

    Omitting ``environ`` means no environment overrides. Outer adapters
    capture ambient state once at their composition boundary and pass it here.
    """

    values = {} if environ is None else environ

    def flag(spec_key: str, env_attr: str) -> bool:
        declared = spec.get(spec_key)
        env_key = getattr(env_keys, env_attr)
        if declared is None:
            return _truthy(values.get(env_key))
        if isinstance(declared, bool):
            return declared
        if isinstance(declared, str):
            normalized = declared.strip().lower()
            if normalized in TRUE_VALUES:
                return True
            if normalized in FALSE_VALUES:
                return False
            raise ValueError(
                f"{spec_key} must be a boolean or one of "
                f"{sorted(TRUE_VALUES | FALSE_VALUES)}, got {declared!r}"
            )
        return bool(declared)

    return GatePolicy(
        forbid_errors=flag("forbid_errors", "forbid_errors"),
        require_corroboration=flag("require_corroboration", "require_corroboration"),
        require_signature=flag("require_signature", "require_signature"),
        signing_key=values.get(env_keys.signing_key),
        require_independent_store=flag("require_independent_store", "require_independent_store"),
    )


def _capture_external_observations(
    spec: Mapping[str, object],
    policy: GatePolicy,
    probe: ExternalProbe | None,
    cid: str,
    handlers: Mapping[str, CheckFn],
) -> dict[int, ExternalObservation]:
    """Perform external probe effects before entering the functional core."""

    if probe is None:
        return {}
    observations: dict[int, ExternalObservation] = {}
    for index, rule in enumerate(expand_rules(spec, policy)):
        key = _kernel_detect_check_key(rule, handlers)
        dispatch_key = "__count__" if key is None else key
        handler = handlers.get(dispatch_key)
        if handler is not _check_external:
            continue
        external = rule.get("external")
        if not isinstance(external, dict):
            continue
        kind = external.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ValueError("external check kind must be a non-empty string")
        # A probe is an effectful, caller-provided port.  Give it its own selector
        # value so it cannot rewrite the captured specification that the kernel will
        # subsequently judge.
        selector = thaw_value(freeze_value(external.get("selector")))
        result = probe.probe(kind, selector, cid)
        observations[index] = ExternalObservation(
            reachable=bool(result.reachable),
            value=result.value,
            complete=bool(getattr(result, "complete", True)),
            separate_source=bool(getattr(result, "separate_source", False)),
            derived_identity=getattr(result, "derived_identity", None),
        )
    return observations


def _capture_custom_check_results(
    evaluation: GateEvaluation,
    probe: ExternalProbe | None,
) -> dict[int, Mapping[str, object]]:
    """Execute extension effects in the shell and capture only their returned values."""

    spec = thaw_value(evaluation.spec)
    events = sorted(
        (thaw_value(event) for event in evaluation.events),
        key=stream_key,
    )
    rules = expand_rules(spec, evaluation.policy)
    indicators = spec.get("indicators") or {}
    allow_errors = tuple(spec.get("allow_errors") or [])
    captured: dict[int, Mapping[str, object]] = {}
    for index, rule in enumerate(rules):
        key = _kernel_detect_check_key(rule, evaluation.registry)
        dispatch_key = "__count__" if key is None else key
        handler = evaluation.registry.get(dispatch_key)
        if handler is None:
            raise ValueError(f"no check handler registered for {dispatch_key!r}")
        if any(handler is canonical for canonical in _CANONICAL_GATE_HANDLERS):
            continue
        context = CheckCtx(
            reachable=evaluation.source.reachable and evaluation.source.complete,
            indicators=indicators,
            ontology=evaluation.ontology,
            allow_errors=allow_errors,
            probe=probe,
            cid=evaluation.source.cid,
        )
        raw = handler(
            [thaw_value(freeze_value(event)) for event in events],
            thaw_value(freeze_value(rule)),
            context,
        )
        if not isinstance(raw, dict):
            name = getattr(handler, "__name__", repr(handler))
            raise ValueError(
                f"check handler {name!r} (key={key!r}) must return a dict with an exact "
                f"bool 'passed' value. Got: {raw!r}"
            )
        captured[index] = raw
    return captured


def evaluate(
    backend: Backend,
    spec: dict,
    *,
    lookback_s: int | None = None,
    future_buffer_s: int | None = None,
    ontology=None,
    clock: Clock | None = None,
    probe=None,
    cid: str | None = None,
    policy: GatePolicy | None = None,
    registry: Mapping[str, CheckFn] | None = None,
    strength_by_key: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
    env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
) -> dict:
    """Run a gate spec once: read the backend, then judge the events.

    Returns ``{ok, reachable, complete, cid, checks:[...], optional_failed:[labels]}``.
    ``ok`` is true iff the store was reachable, the read was complete, and every *required*
    check passed; optional checks that miss are in ``optional_failed`` but never flip ``ok``.
    ``reachable=False`` (store unreachable / INFRA) and ``complete=False`` (truncated read)
    each keep ``ok`` false regardless — neither is a clean pass.

    Each check is evaluated by a streaming monitor (:mod:`ooptdd.monitor`) fed the event
    prefix in store-timestamp order; the per-check dict carries the three-valued ``verdict``
    and the ``settled_at`` stream index alongside the collapsed ``passed``.

    The readback window comes from ``lookback_s`` (arg) else the spec's ``timeWindow``
    (OpenSLO rolling window) else the backend default. ``clock`` (a :class:`Clock`) is
    injectable so the window can be driven deterministically; it defaults to the system clock.
    This function owns the *read*; :func:`evaluate_events` owns the *judgement* and is the
    seam the arrival-poller (:func:`ooptdd.engine.verify.verify_gate`) reuses per poll.
    """
    cid = (
        cid if cid is not None else _resolve_cid(spec, environ, env_keys=env_keys)
    )  # kwarg overrides spec cid/cid_env
    if ontology is None and spec.get("ontology"):
        from ..domain.ontology import Ontology  # file-first; offline, no KG dependency

        ontology = Ontology.from_file(spec["ontology"])
    if lookback_s is None:
        lookback_s = duration_s(spec.get("timeWindow", spec.get("time_window")))
    lookback_s = backend.default_lookback_s if lookback_s is None else lookback_s
    future_buffer_s = (
        backend.default_future_buffer_s if future_buffer_s is None else future_buffer_s
    )
    window = TimeWindow.around_now(clock or SystemClock(), lookback_s, future_buffer_s)
    res = fetch(backend, QuerySpec(cid=cid, window=window))
    # getattr default keeps duck-typed/older result objects (no `complete` field) working.
    return evaluate_events(
        spec,
        res.events,
        reachable=res.reachable,
        complete=getattr(res, "complete", True),
        ontology=ontology,
        cid=cid,
        probe=probe,
        # emit provenance: WHO/WHERE these events came from — stamped into oracle{} (so a green
        # is never a SILENT self-agreement) and used to demote a probe that re-reads this endpoint.
        emit_backend=type(backend).__name__,
        emit_identity=backend_identity(backend),
        # is the store an INDEPENDENT judge (not the SUT's own in-process/same-host writer)?
        # Read from the driver's typed caps — this is what makes `require_independent_store` a
        # real gate instead of dead data (grill A1: caps.independent was never consulted).
        emit_independent=backend_caps(backend).independent,
        # a sampled store cannot prove cross-event causal claims — evidence_tier caps on it
        emit_sampled=backend_caps(backend).samples,
        policy=policy,
        registry=registry,
        strength_by_key=strength_by_key,
        environ=environ,
        env_keys=env_keys,
    )


def evaluate_events(
    spec: dict,
    events: Sequence[Mapping[str, Any]],
    *,
    reachable: bool,
    complete: bool = True,
    ontology=None,
    cid: str | None = None,
    probe=None,
    emit_backend: str | None = None,
    emit_identity: str | None = None,
    emit_independent: bool | None = None,
    emit_sampled: bool = False,
    policy: GatePolicy | None = None,
    registry: Mapping[str, CheckFn] | None = None,
    strength_by_key: Mapping[str, str] | None = None,
    external_observations: Mapping[int, ExternalObservation] | None = None,
    environ: Mapping[str, str] | None = None,
    env_keys: EnvironmentKeys = DEFAULT_ENV_KEYS,
) -> dict:
    """Judge fetched events through a closed, immutable functional-core input.

    This compatibility shell resolves CID and environment defaults, snapshots the
    extension registry, and captures external probe observations.  The actual judgement
    is :func:`judge_events`, which performs no I/O and reads no ambient state.
    """

    captured_spec = thaw_value(freeze_value(spec))
    if isinstance(events, str | bytes) or not isinstance(events, Sequence):
        raise TypeError("gate events must be a sequence of mappings")
    captured_events = [thaw_value(freeze_value(event)) for event in events]
    if not isinstance(captured_spec, dict) or any(
        not isinstance(event, dict) for event in captured_events
    ):
        raise TypeError("gate spec and every event must be mappings")
    resolved_cid = (
        cid if cid is not None else _resolve_cid(captured_spec, environ, env_keys=env_keys)
    )
    resolved_policy = (
        resolve_gate_policy(captured_spec, environ, env_keys=env_keys) if policy is None else policy
    )
    handlers = dict(core_check_registry() if registry is None else registry)
    handlers.setdefault("__count__", _eval_count)
    strengths = dict(check_strengths(handlers))
    if strength_by_key is not None:
        strengths.update(strength_by_key)
    observations = (
        _capture_external_observations(
            captured_spec,
            resolved_policy,
            probe,
            resolved_cid,
            handlers,
        )
        if external_observations is None
        else dict(external_observations)
    )
    evaluation = GateEvaluation.capture(
        captured_spec,
        captured_events,
        policy=resolved_policy,
        source=GateSource(
            cid=resolved_cid,
            reachable=reachable,
            complete=complete,
            emit_backend=emit_backend,
            emit_identity=emit_identity,
            emit_independent=emit_independent,
            emit_sampled=emit_sampled,
        ),
        registry=handlers,
        strength_by_key=strengths,
        external_observations=observations,
        ontology=ontology,
    )
    evaluation = replace(
        evaluation,
        captured_check_results=_capture_custom_check_results(evaluation, probe),
    )
    return judge_events(evaluation).as_dict()


def failed_checks(result: dict) -> list[dict]:
    """The GATING checks that failed — the RED contributors, for programmatic diagnosis. Excludes
    optional/pending checks (they never gate ``ok``). Each carries a stable ``kind`` so a consumer
    keys off ``c["kind"]`` instead of string-matching the raw check shape."""
    return [
        c
        for c in result.get("checks", [])
        if not c.get("passed") and not c.get("optional") and not c.get("pending")
    ]


def green_banner(result: dict) -> str:
    """One honest line for a GREEN gate: WHAT (scope) and HOW HARD (strength) it actually
    asserted, so green is not read as "the system is correct". Pure — shared by the CLI."""
    sc = result.get("scope", {})
    bys = sc.get("by_strength") or {}
    profile = " ".join(f"{k}={v}" for k, v in sorted(bys.items())) or "none"
    line = (
        f"GREEN closed-world over {sc.get('total', 0)} named expectation(s): "
        f"{sc.get('gating', 0)} gating, {sc.get('optional', 0)} optional, "
        f"{sc.get('pending', 0)} pending [by-strength: {profile}]. Certifies the named events "
        "ARRIVED with the asserted shape; does NOT certify the system is correct (un-named "
        f"behavior is unobserved). (cid={result.get('cid')})"
    )
    if sc.get("gating") and set(bys) <= {"existence-only"}:
        line += (
            " WARNING: every gating check is existence-only — proves tokens were emitted, "
            "not that they had any effect."
        )
    if sc.get("stream_coverage") is not None:
        line += (
            f" Stream-coverage: {sc.get('named_event_types')}/"
            f"{sc.get('observed_event_types')} arrived event-type(s) named"
        )
        un = sc.get("unasserted_observed") or []
        line += f" ({len(un)} arrived UNOBSERVED: {','.join(un[:5])})." if un else "."
    orc = result.get("oracle") or {}
    if orc.get("gating"):
        line += (
            " Oracle: single authority — 0 checks corroborated by an independent source "
            "(add an `external:` check to break self-consistency)."
            if orc.get("single_authority")
            else f" Oracle: {orc.get('corroborated')}/{orc.get('gating')} independently"
            " corroborated."
        )
    if sc.get("charge_ratio") is not None:
        line += f" Charge: {sc.get('charged')}/{sc.get('gating')} gating check(s) saw evidence."
    return line


def lint_spec(
    spec: dict,
    *,
    registry: Mapping[str, CheckFn] | None = None,
    strength_by_key: Mapping[str, str] | None = None,
) -> list[dict]:
    """Static, offline strength audit of a gate spec. It runs before event evaluation,
    so a vacuously satisfiable gate is caught before a green
    run. Pure. Returns findings ``[{code, severity, label, message}]`` (``high`` = vacuous/blocking,
    ``medium`` = weak):

    - **VAC0** no expectations at all (`expect:` empty).
    - **VAC1** zero *gating* checks — every check optional/pending; the gate can never fail.
    - **VAC2** `threshold < 1.0` with no `justification:` — a quorum that licenses silent drops.
    - **VAC3** a gating `existence-only` check — proves a token arrived, pins no field/order/forbid.
    """
    rules = list(spec.get("expect", []))
    if not rules:
        return [
            {
                "code": "VAC0",
                "severity": "high",
                "label": "(spec)",
                "message": "empty `expect:` — gate declares no expectations, asserts nothing.",
            }
        ]
    out: list[dict] = []
    gating = [r for r in rules if not r.get("optional") and not r.get("pending")]
    if not gating:
        out.append(
            {
                "code": "VAC1",
                "severity": "high",
                "label": "(spec)",
                "message": "no gating checks — every check is optional/pending; the gate can "
                "never fail (vacuous). Mark at least one check gating.",
            }
        )
    t = spec.get("threshold")
    if t is not None and float(t) < 1.0 and not spec.get("justification"):
        out.append(
            {
                "code": "VAC2",
                "severity": "high",
                "label": "(spec)",
                "message": f"threshold {t} < 1.0 silently licenses dropping up to "
                f"{(1 - float(t)) * 100:.0f}% of expectations every run; add a "
                "`justification:` field if this quorum is intentional.",
            }
        )
    for i, r in enumerate(gating):
        # read the `target:` alias too (grill F4: VAC4 only checked count/want, so a
        # `target: 0` gate escaped with a mere medium finding), and match the FULL tautology
        # set the monitor now flags: `>=`(n<=0), `>`(n<0), `!=`(n<0) — counts are non-negative.
        _op = _norm_op(str(r.get("op", ">="))) if r.get("op") else None
        _cnt = r.get("count", r.get("target", r.get("want", 1)))
        _taut = isinstance(_cnt, (int, float)) and (
            (_op == ">=" and _cnt <= 0) or (_op == ">" and _cnt < 0) or (_op == "!=" and _cnt < 0)
        )
        if _taut:
            out.append(
                {
                    "code": "VAC4",
                    "severity": "high",
                    "label": _label(r),
                    "message": f"check #{i} ({_label(r)}) is `count {_op} {_cnt}` — counts are "
                    "non-negative, so it is always satisfied and can never fail "
                    "(tautology). Use `>= 1`, a `where`, or a real threshold.",
                }
            )
            continue
        if _strength(r, registry, strength_by_key) == "existence-only":
            out.append(
                {
                    "code": "VAC3",
                    "severity": "medium",
                    "label": _label(r),
                    "message": f"check #{i} ({_label(r)}) is existence-only — proves a token "
                    "arrived, pins no field/order/forbid. Add a `where`, "
                    "`must_order`, `absent`, or `invariant` to discriminate.",
                }
            )
    return out


def strength_fingerprint(
    spec: dict,
    *,
    registry: Mapping[str, CheckFn] | None = None,
    strength_by_key: Mapping[str, str] | None = None,
) -> dict:
    """A scalar + profile summary of a gate's discriminating power, computed from the spec alone
    (pure). It is the basis for catching a *weakening* — dropping a `where`, marking a check
    optional/pending, lowering a `threshold` — as a strength regression analogous to a
    coverage drop. This prevents callers from obtaining success by weakening the gate.
    A quorum `threshold < 1` scales the score down (it licenses dropping expectations)."""
    rules = list(spec.get("expect", []))
    gating = [r for r in rules if not r.get("optional") and not r.get("pending")]
    strengths = [_strength(r, registry, strength_by_key) for r in gating]
    raw_threshold = spec.get("threshold")
    threshold = 1.0 if raw_threshold is None else _gate_threshold(raw_threshold)
    raw = sum(_STRENGTH_RANK.get(s, 1) for s in strengths)
    return {
        "gating": len(gating),
        "by_strength": dict(Counter(strengths)),
        "min_threshold": threshold,
        "score": round(raw * threshold, 4),
        # Enforcement posture (spec-declared, pure): the negative/provenance wings that DON'T
        # show up in `expect` strength. Disabling any of these — or WIDENING the allow_errors
        # allowlist — weakens the gate without moving the strength score, so compare_strength
        # must diff them; this closes the hole where a caller flips `require_signature: true`
        # to false (or drops the key) for an unchanged fingerprint.
        "enforcement": {
            "require_signature": bool(spec.get("require_signature")),
            "require_corroboration": bool(spec.get("require_corroboration")),
            "forbid_errors": bool(spec.get("forbid_errors")),
            "allow_errors": len(spec.get("allow_errors") or []),
        },
    }


def compare_strength(baseline: dict, current: dict) -> dict:
    """Did ``current`` get WEAKER than ``baseline``? Returns ``{weakened, regressions[], ...}`` —
    a non-empty ``regressions`` list (fewer gating checks, a lower score/threshold, or a stronger
    check class that disappeared) is a strength regression to report."""
    regs: list[str] = []
    if current["gating"] < baseline["gating"]:
        regs.append(f"gating checks dropped {baseline['gating']} -> {current['gating']}")
    if current["score"] < baseline["score"]:
        regs.append(f"strength score dropped {baseline['score']} -> {current['score']}")
    if current["min_threshold"] < baseline["min_threshold"]:
        regs.append(f"threshold lowered {baseline['min_threshold']} -> {current['min_threshold']}")
    bb, cb = baseline.get("by_strength", {}), current.get("by_strength", {})
    for cls in (
        "invariant",
        "ratio",
        "conformance",
        "liveness",
        "ordered",
        "forbid",
        "value-pinned",
    ):
        if cb.get(cls, 0) < bb.get(cls, 0):
            regs.append(f"{cls} checks dropped {bb.get(cls, 0)} -> {cb.get(cls, 0)}")
    # Enforcement-axis downgrade: disabling a required wing (signature / corroboration /
    # forbid_errors) or WIDENING the allow_errors allowlist weakens the gate without touching
    # the strength score. Guarded on both sides being present so a pre-enforcement baseline
    # (an old fingerprint JSON without this key) never false-flags.
    be, ce = baseline.get("enforcement"), current.get("enforcement")
    if isinstance(be, dict) and isinstance(ce, dict):
        for axis in ("require_signature", "require_corroboration", "forbid_errors"):
            if be.get(axis) and not ce.get(axis):
                regs.append(f"{axis} enforcement dropped {be.get(axis)} -> {ce.get(axis)}")
        if ce.get("allow_errors", 0) > be.get("allow_errors", 0):
            regs.append(
                f"allow_errors widened {be.get('allow_errors', 0)} -> {ce.get('allow_errors', 0)}"
            )
    return {
        "weakened": bool(regs),
        "regressions": regs,
        "baseline_score": baseline["score"],
        "current_score": current["score"],
    }


#: The assertion-strength ladder, low to high. Unlike per-check ``_strength``
#: (one rule's discriminating power), this grades a whole
#: VERDICT by the strongest *kind of evidence* it actually mustered.
EVIDENCE_TIERS = ("local_pass", "emitted", "arrived", "queryable_causal", "external_verdict")


def evidence_tier(result: dict) -> str:
    """Where a verdict sits on the assertion-strength ladder — the formal answer to "what ladder
    prevents fake-green": you can SEE which rung a green reached, computed from its own honesty
    fields (``scope`` charge, per-check ``strength``, ``oracle`` corroboration).

    - ``local_pass``       nothing asserted (vacuous) or the store was unreachable — proves only
                           "the test ran". The fake-green floor.
    - ``emitted``          gating checks exist but none positively witnessed evidence
                           (``charge_ratio == 0``): every one passed on absence/emptiness. Named,
                           not confirmed arrived.
    - ``arrived``          ≥1 gating check positively saw matching evidence (``charge_ratio > 0``):
                           the named events actually landed in the store.
    - ``queryable_causal`` a cross-event consistency relation holds (a passing ``invariant`` /
                           ``metamorphic`` check) — value consistency between events, not counts.
    - ``external_verdict`` an independent oracle corroborated (a separate-source ``external:``
                           check passed): the only rung whose input is NOT the system's own emit.

    Returns the HIGHEST rung the evidence reaches. Orthogonal to ``ok``/RED — it grades the
    evidence on offer, so a green that only reaches ``emitted`` is loudly weak.
    """
    scope = result.get("scope") or {}
    oracle = result.get("oracle") or {}
    if not scope.get("asserts_anything") or not result.get("reachable"):
        return "local_pass"
    if (oracle.get("corroborated") or 0) > 0:
        return "external_verdict"
    # Optional, pending, and tautological checks are observations, not gate authority.
    # Letting one of them promote the tier would allow an unrelated optional invariant to
    # turn an absence-only GREEN into completion-grade causal evidence.
    passing = {
        c.get("strength")
        for c in result.get("checks", [])
        if c.get("passed")
        and not c.get("optional")
        and not c.get("pending")
        and not c.get("tautological")
    }
    if passing & {"invariant", "metamorphic"}:
        # a sampled store (BackendCaps.samples) cannot prove cross-event causal claims —
        # the causal rung caps at `arrived`. external_verdict (above) is untouched: a
        # passing separate-source external: check bypasses the sampled store entirely.
        return "arrived" if result.get("sampled") else "queryable_causal"
    if (scope.get("charge_ratio") or 0) > 0:
        return "arrived"
    return "emitted"


def combine_results(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pure domain-neutral aggregation of independent contract results."""

    def _incomplete(r: Mapping[str, Any]) -> bool:
        return not r["reachable"] or not r.get("complete", True)

    failed = [
        r["cid"] for r in results if r["reachable"] and r.get("complete", True) and not r["ok"]
    ]
    inconclusive = [r["cid"] for r in results if _incomplete(r)]
    pending = {r["cid"]: r["pending_failed"] for r in results if r.get("pending_failed")}
    return {
        "ok": not failed and not inconclusive,
        "failed": failed,
        "inconclusive": inconclusive,
        "pending": pending,
    }
