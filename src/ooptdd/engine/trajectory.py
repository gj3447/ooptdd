"""Agent-trajectory gate predicates — the deterministic slice of the eval-tool vocabulary.

Absorbed *concepts* (implementations original — this repo is AGPL-3.0, sources were not
copied): DeepEval's ``ToolCorrectnessMetric`` is the only fully deterministic agentic
metric in that family, and its value is the three matching modes it names:

  ``exact``    positional sequence equality — extra, missing, or reordered calls all fail
  ``subset``   order-independent recall: each expected call greedily matched to the best
               unmatched observed call (the DeepEval default)
  ``ordered``  weighted longest-common-subsequence — order matters, extras are tolerated

plus Jaccard-weighted partial credit over call arguments (nested dicts recurse). Its
LLM-judge siblings (task completion, plan adherence, argument *reasonableness*, step
efficiency) are deliberately NOT absorbed — they compose through the eval-platform
bridge instead (docs/WEAKNESS_RESOLUTION_PLAN.md §4).

The ooptdd twist: DeepEval scores ``tools_called`` lists the test author hands it —
the agent's self-report. Here the scored calls are the ``gen_ai.execute_tool`` events
that actually **arrived in the store** for this cid, so a fabricated "I called the
tool" claim scores zero unless the event landed. Same vocabulary, stronger evidence.

Spec forms (everything inside the predicate dict; all keys but ``expected`` optional)::

    - tool_calls:
        expected: [search, {name: read_file, args: {path: "a.txt"}}]
        match: subset          # subset (default) | ordered | exact
        compare: [name]        # add "args" to score arguments too
        op: gte                # score comparator (default gte)
        target: 1.0            # score threshold (default 1.0)
        event: gen_ai.execute_tool        # observed-call event name
        name_attr: gen_ai.tool.name       # attr carrying the tool name
        args_attr: gen_ai.tool.call.arguments   # attr carrying call args (dict or JSON)

    - forbidden_tools: [delete_db, shell_exec]   # arrival of any of these = RED

    - aggregate:                       # Phoenix-style trace rollup as a budget gate
        fn: sum                        # sum | max | min | avg
        attr: gen_ai.usage.output_tokens
        event: gen_ai.chat             # optional event-name filter
        op: lte
        target: 50000

Argument values in ``expected`` may be **matchers** instead of literals — the second
absorbed vocabulary, from Phoenix's deterministic tool-path evaluators
(``evals/pxi/evaluators/tools.py`` matcher keys; concept only, original implementation)::

    expected:
      - name: search
        args:
          q: {contains_any: ["cats", "dogs"]}   # substring (str) / membership (list)
          limit: {any: true}                    # key present, value irrelevant
          debug: {absent: true}                 # key must NOT be present

Matcher keys: ``equals, contains_all, contains_any, not_contains, any, non_empty,
absent, empty_or_absent, has_keys``. A dict is a matcher iff it has exactly one key
and that key is a matcher key — any other dict is a literal expectation. When any
expected value is a matcher, that args dict scores **binary** (all conditions hold
-> 1.0, else 0.0 — the Phoenix labels are binary); pure-literal args keep DeepEval's
Jaccard partial credit.

All predicates register through the ``@check`` seam — the kernel is untouched.
"""
from __future__ import annotations

import json

from .gate import _STRENGTH_BY_KEY, CheckCtx, check
from .monitor import _OPS, _norm_op

_DEF_EVENT = "gen_ai.execute_tool"
_DEF_NAME_ATTR = "gen_ai.tool.name"
_DEF_ARGS_ATTR = "gen_ai.tool.call.arguments"

# Discriminating power: tool_calls pins names (and optionally argument values) — value-pinned;
# forbidden_tools is the negative wing — forbid. Registered here so a spec author gets an
# honest strength class without declaring one (setdefault: an explicit ``strength:`` wins).
_STRENGTH_BY_KEY.setdefault("tool_calls", "value-pinned")
_STRENGTH_BY_KEY.setdefault("forbidden_tools", "forbid")
_STRENGTH_BY_KEY.setdefault("aggregate", "threshold")

# ── argument matchers (Phoenix pxi vocabulary, original implementation) ────────

#: Observed args that ARRIVED but could not be read as a dict (corrupt JSON, wrong type).
#: Distinct from None (= the call genuinely carried no args): unreadable evidence must
#: fail CLOSED in every scoring path — treating it as "no args" let an `absent:` matcher
#: pass against a call that DID carry the forbidden key, just in a payload we couldn't
#: parse (grill finding F1).
_UNPARSEABLE = object()


def _m_contains(hay, needle) -> bool:
    """Substring for strings, membership for list/tuple/set/dict — one containment notion."""
    if isinstance(hay, str):
        return isinstance(needle, str) and needle in hay
    if isinstance(hay, (list, tuple, set, dict)):
        return needle in hay
    return False


_MATCHERS = {
    "equals": lambda got, want, present: present and got == want,
    "contains_all": lambda got, want, present: present and all(_m_contains(got, w) for w in want),
    "contains_any": lambda got, want, present: present and any(_m_contains(got, w) for w in want),
    # A key that never arrived contains nothing — prohibition holds on absence (F3).
    "not_contains": lambda got, want, present: (not present)
    or not any(_m_contains(got, w) for w in want),
    "any": lambda got, want, present: present,
    "non_empty": lambda got, want, present: present and bool(got),
    "absent": lambda got, want, present: not present,
    "empty_or_absent": lambda got, want, present: (not present) or not got,
    "has_keys": lambda got, want, present: present and isinstance(got, dict)
    and all(k in got for k in want),
}

#: Matchers whose `want` is a collection of needles. A scalar string here would iterate
#: CHARACTERS (`contains_any: "cats"` passing on any string with an 'a' — grill finding
#: F2), silently gutting the gate — so scalar wants are a loud spec error.
_LIST_WANT_MATCHERS = ("contains_all", "contains_any", "not_contains", "has_keys")


def _is_matcher(v) -> bool:
    return isinstance(v, dict) and len(v) == 1 and next(iter(v)) in _MATCHERS


def _validate_matcher_args(exp_args: dict, *, _top: bool = True) -> None:
    """Spec-time validation: list-want matchers get real lists, and matchers live at the
    TOP level of the args dict only — a matcher nested deeper would be silently scored
    as a literal (always-0 Jaccard, or inverted for `absent`), a fail-closed-but-silent
    trap (F8). Loud beats silent."""
    for key, want in exp_args.items():
        if _is_matcher(want):
            if not _top:
                raise ValueError(
                    f"matcher {want!r} under nested key {key!r}: matchers are only "
                    "supported at the top level of `args` (nest the parent key instead, "
                    "e.g. has_keys/equals on the parent)")
            kind, arg = next(iter(want.items()))
            if kind in _LIST_WANT_MATCHERS and not isinstance(arg, (list, tuple, set)):
                raise ValueError(
                    f"{kind} wants a list of needles, got {arg!r} — a bare string would "
                    "match per-CHARACTER and silently weaken the gate")
        elif isinstance(want, dict):
            _validate_matcher_args(want, _top=False)


def _has_matchers(exp: dict) -> bool:
    return any(_is_matcher(v) for v in exp.values())


def _matcher_args_pass(exp: dict, got) -> bool:
    """Binary matcher-mode scoring: every expected key's condition must hold.

    ``got`` may be a dict, ``None`` (the call genuinely carried no args — presence
    conditions evaluate against emptiness, so ``absent:`` legitimately holds), or
    ``_UNPARSEABLE`` (args arrived but unreadable — fail closed, never assume empty)."""
    if got is _UNPARSEABLE:
        return False
    got = got if isinstance(got, dict) else {}
    for key, want in exp.items():
        present = key in got
        val = got.get(key)
        if _is_matcher(want):
            kind, arg = next(iter(want.items()))
            if not _MATCHERS[kind](val, arg, present):
                return False
        elif not (present and val == want):  # literal key inside a matcher-mode dict
            return False
    return True


def _norm_expected(items) -> list[tuple[str, dict | None]]:
    """``[str | {name, args}]`` -> ``[(name, args-or-None)]``. Malformed entries are loud,
    and so is emptiness: an empty expectation can never fail — a tautology masquerading
    as a value-pinned check (F4)."""
    if not items:
        raise ValueError(
            "tool_calls `expected` must be non-empty — an empty expectation is always "
            "green (assert absence with `forbidden_tools`/`absent` instead)")
    out: list[tuple[str, dict | None]] = []
    for it in items:
        if isinstance(it, str):
            out.append((it, None))
        elif isinstance(it, dict) and it.get("name"):
            args = it.get("args")
            if args is not None:
                if not isinstance(args, dict):
                    raise ValueError(f"tool_calls expected args must be a dict: {it!r}")
                _validate_matcher_args(args)
            out.append((str(it["name"]), args))
        else:
            raise ValueError(f"tool_calls expected entry must be str or {{name,...}}: {it!r}")
    return out


def _observed_calls(events: list, event_name: str, name_attr: str,
                    args_attr: str) -> list[tuple[str, object]]:
    """The arrival-asserted call sequence: (name, args) per matching event, stream order.
    Args may arrive as a dict or a JSON string (OTLP attribute values are strings).
    Args that arrived but cannot be read as a dict become ``_UNPARSEABLE`` — kept
    distinct from ``None`` (no args at all) so unreadable evidence fails closed (F1)."""
    out: list[tuple[str, object]] = []
    for ev in events:
        if ev.get("event") != event_name:
            continue
        name = ev.get(name_attr)
        if name is None:
            continue  # conformance (`conforms:`) owns flagging a nameless tool event
        args = ev.get(args_attr)
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = _UNPARSEABLE
        if args is not None and args is not _UNPARSEABLE and not isinstance(args, dict):
            args = _UNPARSEABLE  # arrived, but not a readable args dict
        out.append((str(name), args))
    return out


def _arg_credit(exp: dict, got, *, exact: bool) -> float:
    """Jaccard-weighted key overlap, recursing into nested dicts; ``exact`` binarizes."""
    if got is None or got is _UNPARSEABLE or not isinstance(got, dict):
        return 0.0
    if exp == got:
        return 1.0
    if exact:
        return 0.0
    total = set(exp) | set(got)
    if not total:
        return 1.0
    score = 0.0
    for key in set(exp) & set(got):
        if exp[key] == got[key]:
            score += 1 / len(total)
        elif isinstance(exp[key], dict) and isinstance(got[key], dict):
            score += _arg_credit(exp[key], got[key], exact=False) / len(total)
    return score


def _pair_score(exp: tuple[str, dict | None], got: tuple[str, dict | None],
                *, with_args: bool, exact: bool) -> float:
    """0..1 match quality of one expected call against one observed call."""
    if exp[0] != got[0]:
        return 0.0
    if not with_args or exp[1] is None:  # no expected args pinned -> name match suffices
        return 1.0
    if _has_matchers(exp[1]):  # matcher mode is binary (Phoenix labels are pass/fail)
        return 1.0 if _matcher_args_pass(exp[1], got[1]) else 0.0
    return _arg_credit(exp[1], got[1], exact=exact)


def _score_exact(expected, observed, *, with_args: bool) -> tuple[float, list[str]]:
    if len(expected) != len(observed):
        return 0.0, [n for n, _ in expected]
    if not expected:
        return 1.0, []
    bad = [e[0] for e, o in zip(expected, observed, strict=True)
           if _pair_score(e, o, with_args=with_args, exact=True) < 1.0]
    return (0.0, bad) if bad else (1.0, [])


def _score_subset(expected, observed, *, with_args: bool) -> tuple[float, list[str]]:
    """Greedy recall: each expected call claims its best-scoring unmatched observed call."""
    if not expected:
        return 1.0, []
    if not observed:
        return 0.0, [n for n, _ in expected]
    total, missing, taken = 0.0, [], set()
    for exp in expected:
        best, best_j = 0.0, None
        for j, got in enumerate(observed):
            if j in taken:
                continue
            s = _pair_score(exp, got, with_args=with_args, exact=False)
            if s > best:
                best, best_j = s, j
        if best_j is not None:
            total += best
            taken.add(best_j)
        else:
            missing.append(exp[0])
    return total / len(expected), missing


def _score_ordered(expected, observed, *, with_args: bool) -> tuple[float, list[str]]:
    """Weighted LCS: credit accrues only along a common subsequence, so order violations
    lose the out-of-order call's weight while extras cost nothing."""
    if not expected:
        return 1.0, []
    n, m = len(expected), len(observed)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            w = _pair_score(expected[i - 1], observed[j - 1], with_args=with_args, exact=False)
            dp[i][j] = max(dp[i - 1][j], dp[i][j - 1], (dp[i - 1][j - 1] + w) if w > 0 else 0.0)
    # names not observed at all are reported missing (order-loss alone isn't "missing")
    seen = {g[0] for g in observed}
    missing = [e[0] for e in expected if e[0] not in seen]
    return dp[n][m] / n, missing


_SCORERS = {"exact": _score_exact, "subset": _score_subset, "ordered": _score_ordered}


def _resolve_op(raw) -> str:
    """Normalize an op and fail loudly on an unknown one — a typo'd op must be a spec
    error, not a bare KeyError from deep inside the scorer."""
    op = _norm_op(str(raw))
    if op not in _OPS:
        raise ValueError(f"unknown op {raw!r}; one of {sorted(_OPS)} (or gte/gt/eq/ne/lte/lt)")
    return op


@check("tool_calls")
def _check_tool_calls(events: list, rule: dict, ctx: CheckCtx) -> dict:
    spec = rule["tool_calls"]
    if not isinstance(spec, dict) or "expected" not in spec:
        raise ValueError("tool_calls requires a dict with an `expected:` list")
    match = spec.get("match", "subset")
    if match not in _SCORERS:
        raise ValueError(f"tool_calls match must be one of {sorted(_SCORERS)}: {match!r}")
    compare = spec.get("compare", ["name"])
    if isinstance(compare, str):  # YAML `compare: name,args` — split, don't substring-match
        compare = [c.strip() for c in compare.split(",")]
    unknown_compare = set(compare) - {"name", "args"}
    if unknown_compare:
        raise ValueError(f"tool_calls compare entries must be name/args: {sorted(unknown_compare)}")
    with_args = "args" in compare
    expected = _norm_expected(spec["expected"])
    observed = _observed_calls(events, spec.get("event", _DEF_EVENT),
                               spec.get("name_attr", _DEF_NAME_ATTR),
                               spec.get("args_attr", _DEF_ARGS_ATTR))
    score, missing = _SCORERS[match](expected, observed, with_args=with_args)
    op = _resolve_op(spec.get("op", ">="))
    target = float(spec.get("target", 1.0))
    return {
        "label": f"tool_calls:{match}",
        "tool_calls": [n for n, _ in expected],
        "match": match, "compare": list(compare),
        "score": round(score, 4), "op": op, "target": target,
        "observed": [n for n, _ in observed], "missing": missing,
        "passed": bool(_OPS[op](score, target)),
        "charged": bool(observed),  # honesty: evidence = observed tool arrivals, not absence
    }


_AGG_FNS = {
    "sum": sum,
    "max": max,
    "min": min,
    "avg": lambda vals: sum(vals) / len(vals),
}


@check("aggregate")
def _check_aggregate(events: list, rule: dict, ctx: CheckCtx) -> dict:
    """Numeric rollup over arrived events — Phoenix materializes cumulative token/error
    counts per trace subtree; here the same idea is a gate: sum/max/min/avg of an attr
    across the cid's events vs a budget. (Also the deterministic stand-in for DeepEval's
    step-efficiency family: bound the tool-call count with a plain count check, bound
    the spend with ``aggregate: {fn: sum, attr: gen_ai.usage.output_tokens}``.)"""
    spec = rule["aggregate"]
    if not isinstance(spec, dict):
        raise ValueError("aggregate requires a dict: {fn, attr, target, [event, op]}")
    fn = spec.get("fn", "sum")
    if fn not in _AGG_FNS:
        raise ValueError(f"aggregate fn must be one of {sorted(_AGG_FNS)}: {fn!r}")
    if "attr" not in spec or "target" not in spec:
        raise ValueError("aggregate requires `attr:` and `target:`")
    attr = spec["attr"]
    event_name = spec.get("event")
    vals = []
    for ev in events:
        if event_name is not None and ev.get("event") != event_name:
            continue
        v = ev.get(attr)
        if isinstance(v, str):
            # OTLP attribute values arrive stringified; a numeric string is evidence,
            # and skipping it would turn a real token stream into a vacuous green (F5).
            try:
                v = float(v)
            except ValueError:
                continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue  # non-numeric (or bool) attr values never silently count
        vals.append(v)
    op = _resolve_op(spec.get("op", "<="))
    target = float(spec["target"])
    base = {"label": f"aggregate:{fn}({attr})", "aggregate": fn, "attr": attr,
            "op": op, "target": target, "n": len(vals), "charged": bool(vals)}
    if not vals:
        # No observations: a sum-budget is vacuously honored (0 spend) but SURFACED —
        # uncharged + reason — so the honesty layer and a reader both see "green on no
        # evidence" (a typo'd attr never charges; watch scope.uncharged). Other fns
        # have no honest zero, so they fail.
        value = 0.0 if fn == "sum" else None
        passed = bool(_OPS[op](0.0, target)) if fn == "sum" else False
        return {**base, "value": value, "passed": passed, "reason": "aggregate_no_values"}
    value = _AGG_FNS[fn](vals)
    return {**base, "value": value, "passed": bool(_OPS[op](value, target))}


@check("forbidden_tools")
def _check_forbidden_tools(events: list, rule: dict, ctx: CheckCtx) -> dict:
    names = rule["forbidden_tools"]
    if isinstance(names, str):
        names = [names]
    if not names:
        raise ValueError(
            "forbidden_tools must name at least one tool — an empty prohibition can "
            "never fail (a tautology wearing the `forbid` strength class)")
    forbidden = {str(n) for n in names}
    observed = _observed_calls(events, rule.get("event", _DEF_EVENT),
                               rule.get("name_attr", _DEF_NAME_ATTR),
                               rule.get("args_attr", _DEF_ARGS_ATTR))
    offenders = [n for n, _ in observed if n in forbidden]
    return {
        "label": "forbidden_tools:" + ",".join(sorted(forbidden)),
        "forbidden_tools": sorted(forbidden), "offenders": offenders,
        "violations": len(offenders),
        "passed": not offenders,
        "charged": bool(offenders),  # mirrors `absent`: charged only when it SAW an offender
    }
