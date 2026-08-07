"""Gate mutation testing — does the gate actually discriminate, or is it vacuously green?

ooptdd's honest limit: *"a gate that only checks existence won't catch a wrong value."*
A gate that asserts ``present: [{event: cycle}]`` passes whether the cycle's verdict is
PASS or NG — it has a blind spot. This module quantifies that. Given a *passing*
(events, gate) pair, it derives the deviations the gate **should** catch and re-runs the
gate on each:

  * ``drop:<x>``     — remove the events satisfying a required expectation. A gate that
                       required them must go RED.
  * ``corrupt:<x>``  — for a ``where``-constrained expectation, change the matched field
                       value. A gate that constrains that value must go RED; one that only
                       checks existence stays GREEN — a **surviving mutant = a blind spot**.
  * ``rename_required_tool`` — rename a required arrived tool call. ``tool_calls`` must
                               catch the missing requirement.
  * ``corrupt_required_args`` — corrupt an argument pinned by ``tool_calls``.
  * ``reorder_required_tools`` — swap two required calls for ``ordered``/``exact`` gates.
  * ``inject_exact_extra`` — add an unregistered call to an ``exact`` trajectory.
  * ``inject_forbidden_tool`` / ``inject_forbidden_call`` — materialize the negative
                               trajectory wing instead of assigning it a vacuous score.
  * ``inject_error`` — add an ERROR-level record. Caught only when the gate forbids errors
                       (``forbid_errors`` / ``OOPTDD_FORBID_ERRORS`` / an ``absent`` rule).

The mutation *score* is caught / total; survivors name exactly which deviation the gate
waved through.  Mutants are derived only when the rule semantics say the deviation should
change the verdict: an ``ordered`` rule gets a reorder mutant, while a ``subset`` rule does
not; negative wings get injection mutants, never meaningless drop mutants.

This is the Schemathesis idea (systematic spec-derived negative cases) applied to gates
rather than APIs. From the ooptdd-oss prometheus cycle (A5,
seed-ooptdd-negwing-mutant-allowlist-20260618).
"""
from __future__ import annotations

import hashlib
import json
import math
import os

from .backends.memory import MemoryBackend, reset
from .engine.gate import _matches, _resolve_matcher, evaluate

_SENTINEL = "__ooptdd_mutant__"
_DEF_TOOL_EVENT = "gen_ai.execute_tool"
_DEF_TOOL_NAME = "gen_ai.tool.name"
_DEF_TOOL_ARGS = "gen_ai.tool.call.arguments"
_MISSING = object()


def _forbids_errors(spec: dict) -> bool:
    fe = spec.get("forbid_errors")
    if fe is None:
        env = str(os.getenv("OOPTDD_FORBID_ERRORS", "")).strip().lower()
        fe = env in {"1", "true", "yes", "on"}
    if fe:
        return True
    indicators = spec.get("indicators") or {}
    for rule in spec.get("expect", []):
        raw = rule.get("absent", rule.get("forbid"))
        if raw is None:
            continue
        for m in (raw if isinstance(raw, list) else [raw]):
            _, where = _resolve_matcher(m, indicators)
            lvl = str(where.get("level", "")).upper()
            if lvl in {"ERROR", "CRITICAL"} or "level" not in where:
                return True
    return False


def _label(event, where) -> str:
    return event or ("where:" + ",".join(f"{k}={v}" for k, v in where.items())) or "(any)"


def _tool_item(item) -> tuple[str, dict | None] | None:
    if isinstance(item, str):
        return item, None
    if isinstance(item, dict) and item.get("name"):
        args = item.get("args")
        return str(item["name"]), args if isinstance(args, dict) else None
    return None


def _tool_fields(rule: dict, nested: dict | None = None) -> tuple[str, str, str]:
    cfg = nested or rule
    return (
        str(cfg.get("event", rule.get("event", _DEF_TOOL_EVENT))),
        str(cfg.get("name_attr", rule.get("name_attr", _DEF_TOOL_NAME))),
        str(cfg.get("args_attr", rule.get("args_attr", _DEF_TOOL_ARGS))),
    )


def _matcher_witness(want):
    """Materialize one value satisfying a supported trajectory matcher when possible.

    This is deliberately small and deterministic.  Unsupported or contradictory composed
    matchers return ``_MISSING``; the caller can still generate the corrupt-arguments
    fail-closed mutant, so the eligible denominator never depends on guesswork.
    """
    if not isinstance(want, dict) or not want:
        return want
    matcher_keys = {
        "equals", "contains_all", "contains_any", "not_contains", "any",
        "non_empty", "absent", "empty_or_absent", "has_keys",
    }
    if not set(want) <= matcher_keys:
        return want
    if "absent" in want or "empty_or_absent" in want:
        return _MISSING
    # A composed matcher needs a witness for the *intersection* of its constraints.
    # Picking the first ``contains_any`` item is unsound when another constraint rejects
    # that item (for example contains_any=[safe, danger] + not_contains=[safe]). Build a
    # compact candidate set, then ask the production matcher to prove the candidate.
    from .engine.trajectory import _matcher_args_pass

    candidates: list[object] = []

    def candidate(value) -> None:
        if not any(value == existing for existing in candidates):
            candidates.append(value)

    if "equals" in want:
        candidate(want["equals"])
    contains_all = list(want.get("contains_all") or [])
    contains_any = list(want.get("contains_any") or [])
    has_keys = [str(key) for key in (want.get("has_keys") or [])]
    if contains_all:
        candidate(" ".join(str(item) for item in contains_all))
        candidate(list(contains_all))
        candidate({str(item): True for item in contains_all})
    for item in contains_any:
        candidate(str(item))
        candidate([item])
        candidate({str(item): True})
    if has_keys:
        candidate({key: True for key in has_keys})
    if has_keys or contains_all:
        candidate({key: True for key in [*has_keys, *(str(x) for x in contains_all)]})
    for fallback in (_SENTINEL, "__ooptdd_safe_witness__", 1, ["__ooptdd_safe_witness__"]):
        candidate(fallback)

    expected = {"value": want}
    for candidate_value in candidates:
        if _matcher_args_pass(expected, {"value": candidate_value}):
            return candidate_value
    return _MISSING


def _args_witness(expected: dict) -> dict:
    out = {}
    for key, want in expected.items():
        value = _matcher_witness(want)
        if value is not _MISSING:
            out[key] = value
    return out


def _corrupt_args(expected: dict, observed) -> dict | None:
    """Return one observed-args variant that the expected matcher rejects.

    A scalar sentinel is not a universal corruption: it still satisfies ``non_empty`` and
    ``any``, and often satisfies ``not_contains``. Generate matcher-aware candidates and
    confirm the whole expected args object rejects the candidate before admitting it to
    the eligible denominator.
    """
    from .engine.trajectory import _matcher_args_pass

    if isinstance(observed, str):
        try:
            observed = json.loads(observed)
        except ValueError:
            observed = None
    base = dict(observed) if isinstance(observed, dict) else {}
    for key, want in expected.items():
        candidates = []
        removed = {field: value for field, value in base.items() if field != key}
        candidates.append(removed)
        if isinstance(want, dict):
            if want.get("not_contains"):
                candidates.append({**base, key: str(next(iter(want["not_contains"])))})
            if "absent" in want or "empty_or_absent" in want:
                candidates.append({**base, key: _SENTINEL})
            if "has_keys" in want:
                candidates.append({**base, key: {}})
            if "non_empty" in want or "any" in want:
                candidates.append({**base, key: ""})
            if "contains_all" in want or "contains_any" in want or "equals" in want:
                candidates.append({**base, key: _SENTINEL})
        else:
            replacement = _SENTINEL if want != _SENTINEL else "__ooptdd_mutant_2__"
            candidates.append({**base, key: replacement})
        for candidate in candidates:
            if not _matcher_args_pass(expected, candidate):
                return candidate
    return None


def _mutation_id(label: str, events: list[dict]) -> str:
    payload = json.dumps({"label": label, "events": events}, sort_keys=True,
                         ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def derive_mutations(events: list[dict], spec: dict) -> list[tuple[str, list[dict]]]:
    """Labeled mutant event-lists derived from the gate's own expectations — each a
    deviation the gate ought to catch. Deduplicated by label."""
    indicators = spec.get("indicators") or {}
    out: list[tuple[str, list[dict]]] = []
    seen: set[str] = set()

    def add(label: str, mevents: list[dict]) -> None:
        if label not in seen and mevents != events:
            seen.add(label)
            out.append((label, mevents))

    def cover(matcher: dict) -> None:
        event, where = _resolve_matcher(matcher, indicators)
        # drop: remove every event satisfying this expectation
        add("drop:" + _label(event, where), [e for e in events if not _matches(e, event, where)])
        # corrupt: flip the first where-field so matching events no longer match (value check)
        if where:
            field = next(iter(where))
            add(
                f"corrupt:{event or 'where'}.{field}",
                [{**e, field: _SENTINEL} if _matches(e, event, where) else e for e in events],
            )

    def cover_tool_calls(rule: dict) -> None:
        cfg = rule.get("tool_calls")
        if not isinstance(cfg, dict):
            return
        expected: list[tuple[str, dict | None]] = []
        for raw_item in cfg.get("expected", []):
            item = _tool_item(raw_item)
            if item is not None:
                expected.append(item)
        event_name, name_attr, args_attr = _tool_fields(rule, cfg)

        compare = cfg.get("compare", ["name"])
        if isinstance(compare, str):
            compare = [part.strip() for part in compare.split(",")]
        match = cfg.get("match", "subset")

        # Bind every expected entry to one distinct arrived call. Stable expected indices
        # keep duplicate name/argument requirements from collapsing into one mutation label.
        from .engine.trajectory import _observed_calls, _pair_score

        observed: list[tuple[int, tuple[str, object]]] = []
        for event_index, ev in enumerate(events):
            calls = _observed_calls([ev], event_name, name_attr, args_attr)
            if calls:
                observed.append((event_index, calls[0]))
        matched_indices: list[int | None] = [None] * len(expected)
        with_args = "args" in compare
        if match == "exact" and len(observed) == len(expected):
            for expected_index, (exp, (event_index, got)) in enumerate(
                zip(expected, observed, strict=True)
            ):
                if _pair_score(exp, got, with_args=with_args, exact=True) > 0:
                    matched_indices[expected_index] = event_index
        elif match == "ordered":
            cursor = 0
            for expected_index, exp in enumerate(expected):
                for observed_index in range(cursor, len(observed)):
                    event_index, got = observed[observed_index]
                    if _pair_score(exp, got, with_args=with_args, exact=False) > 0:
                        matched_indices[expected_index] = event_index
                        cursor = observed_index + 1
                        break
        else:
            taken: set[int] = set()
            for expected_index, exp in enumerate(expected):
                best_score, best_observed = 0.0, None
                for observed_index, (event_index, got) in enumerate(observed):
                    if observed_index in taken:
                        continue
                    score = _pair_score(exp, got, with_args=with_args, exact=False)
                    if score > best_score:
                        best_score, best_observed = score, (observed_index, event_index)
                if best_observed is not None:
                    observed_index, event_index = best_observed
                    taken.add(observed_index)
                    matched_indices[expected_index] = event_index

        # Mutate one bound call at a time. This exposes tolerance/duplicate blind spots and
        # gives every eligible mutant an unambiguous target identity.
        for expected_index, (name, args) in enumerate(expected):
            matched_event_index = matched_indices[expected_index]
            if matched_event_index is None:
                continue
            renamed = list(events)
            renamed[matched_event_index] = {
                **renamed[matched_event_index],
                name_attr: _SENTINEL,
            }
            add(f"rename_required_tool:{expected_index}:{name}", renamed)
            if args and "args" in compare:
                for key in args:
                    mutated_args = _corrupt_args(
                        {key: args[key]},
                        events[matched_event_index].get(args_attr),
                    )
                    if mutated_args is not None:
                        corrupted = list(events)
                        corrupted[matched_event_index] = {
                            **corrupted[matched_event_index],
                            args_attr: mutated_args,
                        }
                        add(
                            f"corrupt_required_args:{expected_index}:{name}.{key}",
                            corrupted,
                        )

        if match in {"ordered", "exact"} and len(expected) >= 2:
            for pair_index in range(len(expected) - 1):
                first, second = matched_indices[pair_index:pair_index + 2]
                if first is None or second is None or first == second:
                    continue
                reordered = list(events)
                reordered[first], reordered[second] = reordered[second], reordered[first]
                first_name, second_name = expected[pair_index][0], expected[pair_index + 1][0]
                add(
                    f"reorder_required_tools:{pair_index}:{first_name}>{second_name}",
                    reordered,
                )
        if match == "exact":
            add("inject_exact_extra", [*events, {
                "event": event_name, name_attr: _SENTINEL, args_attr: {},
            }])

    def cover_forbidden_tools(rule: dict) -> None:
        raw = rule.get("forbidden_tools")
        names = [raw] if isinstance(raw, str) else list(raw or [])
        if not names:
            return
        event_name, name_attr, args_attr = _tool_fields(rule)
        for raw_name in names:
            name = str(raw_name)
            add(f"inject_forbidden_tool:{name}", [*events, {
                "event": event_name, name_attr: name, args_attr: {},
            }])

    def cover_forbidden_tool_calls(rule: dict) -> None:
        raw = rule.get("forbidden_tool_calls")
        items = raw if isinstance(raw, list) else [raw]
        event_name, name_attr, args_attr = _tool_fields(rule)
        corrupt_names: set[str] = set()
        for index, raw_item in enumerate(items):
            item = _tool_item(raw_item) if raw_item is not None else None
            if item is None:
                continue
            name, args = item
            witness = _args_witness(args) if args else {}
            if args:
                # Only admit a semantic witness when the gate's own matcher confirms it.
                from .engine.trajectory import _forbidden_args_pass

                matched, _ = _forbidden_args_pass(args, witness)
                if matched:
                    add(f"inject_forbidden_call:{index}:{name}", [*events, {
                        "event": event_name, name_attr: name, args_attr: witness,
                    }])
                # Unreadable args exercise one name-level fail-closed guarantee; do not count
                # duplicate identical corrupt payloads for multiple policies on the same name.
                if name not in corrupt_names:
                    add(f"inject_forbidden_call_corrupt_args:{name}", [*events, {
                        "event": event_name, name_attr: name, args_attr: "{",
                    }])
                    corrupt_names.add(name)
            else:
                add(f"inject_forbidden_call:{index}:{name}", [*events, {
                    "event": event_name, name_attr: name, args_attr: {},
                }])

    for rule in spec.get("expect", []):
        if rule.get("optional") or rule.get("pending"):
            continue  # non-gating; a gate that ignores it by design isn't a blind spot
        if "present" in rule:
            for m in rule["present"]:
                cover(m)
        elif "must_order" in rule or "trajectory" in rule:
            seq = rule.get("must_order") or rule.get("trajectory")
            if seq:
                cover({"event": seq[0]})  # drop the first required step
        elif "tool_calls" in rule:
            cover_tool_calls(rule)
        elif "forbidden_tools" in rule:
            cover_forbidden_tools(rule)
        elif "forbidden_tool_calls" in rule:
            cover_forbidden_tool_calls(rule)
        elif not any(k in rule for k in
                     ("absent", "forbid", "ratioMetric", "conforms", "heartbeat",
                      # Trajectory predicates are handled above with semantic operators.
                      # Aggregate still needs op-aware witness synthesis; exclusion is more
                      # honest than a generic drop mutant that may preserve its budget.
                      "tool_calls", "forbidden_tools", "forbidden_tool_calls", "aggregate")):
            cover(rule)  # a plain count/where rule

    if _forbids_errors(spec):
        add("inject_error", [*events, {"event": "_mutant.error", "level": "ERROR",
                                       "error": "injected mutant fault"}])
    return out


def _run(events: list[dict], spec: dict, run_cid: str) -> bool:
    b = MemoryBackend()
    b.ship([{**e, "cid": run_cid} for e in events])
    return bool(evaluate(b, {**spec, "cid": run_cid})["ok"])


def mutation_report(events: list[dict], spec: dict) -> dict:
    """Run the gate on ``events`` (baseline) and on each derived mutant.

    Returns ``{baseline_green, mutations:[{mutation, caught}], survivors:[label], score, n}``.
    ``baseline_green=False`` means the inputs don't even pass — the score is meaningless
    until you fix that. ``survivors`` are the deviations the gate let through: its blind
    spots. Uses (and resets) the in-memory store; each run gets a unique cid so runs never
    cross-contaminate.
    """
    reset()
    baseline_green = _run(events, spec, "mut-baseline")
    rows = []
    for i, (label, mevents) in enumerate(derive_mutations(events, spec)):
        caught = not _run(mevents, spec, f"mut-{i}")
        rows.append({
            "mutation_id": _mutation_id(label, mevents),
            "mutation": label,
            "operator": label.split(":", 1)[0],
            "status": "killed" if caught else "survived",
            "caught": caught,
        })
    survivors = [r["mutation"] for r in rows if not r["caught"]]
    score = round((len(rows) - len(survivors)) / len(rows), 3) if rows else 1.0
    # The drop-ALL canary: run the gate on an EMPTY stream. If it still passes, the
    # gate has no gating positive expectation — vacuity PROVEN by measurement (the
    # dynamic cross-check of the static lint/strength `vacuous` signals). In this pure
    # data-list model that is what a surviving drop-everything mutant means — there is
    # no external test runner whose brokenness it could indicate (contrast mutmut's
    # forced-fail subprocess check). Not counted into `score`: it grades the GATE's
    # shape, not a deviation the gate should catch.
    canary_survived = _run([], spec, "mut-canary")
    return {
        "baseline_green": baseline_green,
        "mutations": rows,
        "survivors": survivors,
        "score": score,
        "score_status": "measured" if rows else "unmeasured",
        "n": len(rows),
        "eligible": len(rows),
        "status_counts": {
            "killed": len(rows) - len(survivors),
            "survived": len(survivors),
        },
        "canary_survived": canary_survived,
    }


LOCK_SCHEMA = "ooptdd-mutation-lock/v1"


def verify_mutation_lock(spec_bytes: bytes, lock: dict, cli_min_score: float | None = None):
    """Winner's-curse lock: bind the mutation threshold to a spec committed BEFORE the run.

    The curse this refuses: run ``mutate``, look at the score, then pick the threshold
    (or quietly edit the gate) so the number reads as a pass. A lock file carries the
    gate spec's sha256 and the threshold, is committed before any scoring run, and this
    verifier makes the grading refuse anything that moved after the peek.

    Returns ``(min_score, None)`` when the lock binds, or ``(None, reason)`` when the
    run must not be graded — the CLI maps every reason to exit 2 (setup refusal, not a
    measured verdict). A CLI ``--min-score`` differing from the locked value IS the
    re-pick, so it is refused rather than tie-broken.
    """
    if lock.get("schema") != LOCK_SCHEMA:
        return None, f"unrecognized lock schema: {lock.get('schema')!r}"
    try:
        locked_score = float(lock["min_score"])
        locked_sha = str(lock["gate_spec_sha256"])
    except (KeyError, TypeError, ValueError):
        return None, "lock missing/invalid min_score or gate_spec_sha256"
    actual = hashlib.sha256(spec_bytes).hexdigest()
    if actual != locked_sha:
        return None, (f"gate spec does not match the lock (locked {locked_sha[:12]}..., "
                      f"actual {actual[:12]}...) — re-lock deliberately, then rerun")
    if cli_min_score is not None and cli_min_score != locked_score:
        return None, (f"--min-score {cli_min_score} conflicts with the locked threshold "
                      f"{locked_score} — the threshold cannot be re-picked after the score "
                      "is visible")
    return locked_score, None


# ── audit-ranking gate (POST_TIER1_ARC front A4) ───────────────────────────────────────
#
# Premise correction, on the record: the arc harness asked for "nDCG over ranked mutation
# kills", but a mutation report has never carried a ranking — ``mutations`` is the
# derivation-order list, unranked and unweighted. The honest reinterpretation fixed here:
# the *canonical ranking authority* is the derivation order itself (authenticated row by
# row through ``_mutation_id`` re-derivation), relevance is the MEASURED kill status
# (caught=1, survived=0 — no invented grades), and the ranked-kill view is the stable
# (caught desc, canonical position asc) sort. One mathematical fact shapes the whole
# design: nDCG is invariant under permutations within an equal relevance grade, so on an
# all-killed report NO permutation moves the number. The id-sequence authentication layer
# is therefore the actual integrity defence; the nDCG value only grades order among MIXED
# relevances (``order_sensitive`` says whether it could have failed at all).


def ndcg(relevances, k: int | None = None) -> float | None:
    """Textbook nDCG over an already-ordered relevance list.

    ``DCG = sum(rel_i / log2(i + 2))`` (0-based ``i``); ``IDCG`` is the DCG of the same
    relevances sorted descending; ``k`` truncates both. ``IDCG == 0`` (nothing relevant
    anywhere) makes the ratio UNDEFINED and returns ``None`` — the caller must map that
    to a refusal, never fill in 0.0 or 1.0.
    """
    rels = [float(r) for r in relevances]
    top = rels if k is None else rels[:k]
    ideal = sorted(rels, reverse=True)[:len(top)]
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(top))
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(ideal))
    if idcg == 0:
        return None
    return round(dcg / idcg, 6)


def ranked_kills(report: dict) -> list[dict]:
    """The canonical ranked-kill view of a mutation report: kills first, then survivors,
    ties broken by canonical (derivation) position — a stable sort, so equal-relevance
    rows keep the order the authority layer can authenticate. ``relevance`` is the
    measured kill status (1 caught / 0 survived), nothing finer is invented."""
    rows = report.get("mutations") or []
    order = sorted(range(len(rows)), key=lambda i: (not rows[i].get("caught"), i))
    return [{
        "rank": rank,
        "position": i,
        "relevance": 1 if rows[i].get("caught") else 0,
        **{key: rows[i].get(key)
           for key in ("mutation_id", "mutation", "operator", "status", "caught")},
    } for rank, i in enumerate(order, start=1)]


def _ranking_refusal(reason: str, n: int) -> dict:
    # A refused ranking never even produces a number to be tempted by (the A3 pattern).
    return {"ok": False, "reason": reason, "ndcg": None, "order_sensitive": None, "n": n}


def verify_audit_ranking(report: dict, spec: dict, events: list[dict],
                         published_ids: list[str] | None = None) -> dict:
    """Authenticate a mutation report's ranking against its own (spec, events) origin,
    then score the published order with nDCG.

    Authentication is positional: ``derive_mutations(events, spec)`` is re-derived and
    every report row's ``mutation_id`` must equal the re-computed id at that position.
    A permuted, truncated, padded, hand-written report — or one produced from a
    different (spec, events) pair — fails HERE, before any number exists. This layer is
    what actually catches reordering: nDCG cannot (see the section comment above).

    ``published_ids`` is the ranking under audit (default: the report's own row order).
    It must be an exact multiset permutation of the authenticated rows — a ranking that
    drops audited rows or smuggles in foreign ids is refused, not scored. Relevance per
    id is the report's measured ``caught``. Returns ``{ok, reason, ndcg,
    order_sensitive, n}``; ``order_sensitive`` is True only when relevances are mixed —
    on a homogeneous (all-killed / all-survived) list the nDCG value carries no order
    information and says so.
    """
    rows = report.get("mutations")
    if not isinstance(rows, list) or not rows:
        return _ranking_refusal("report carries no mutations rows — nothing to rank", 0)
    derived = derive_mutations(events, spec)
    if len(rows) != len(derived):
        return _ranking_refusal(
            f"report carries {len(rows)} rows but (spec, events) derives {len(derived)} "
            "mutants — not this audit's output", len(rows))
    for pos, (row, (label, mevents)) in enumerate(zip(rows, derived, strict=True)):
        row_id = row.get("mutation_id") if isinstance(row, dict) else None
        if not row_id:
            return _ranking_refusal(
                f"row {pos} has no mutation_id — provenance missing, unaudited", len(rows))
        expected = _mutation_id(label, mevents)
        if row_id != expected:
            return _ranking_refusal(
                f"row {pos} mutation_id {row_id} != re-derived {expected} — permuted, "
                "forged, or from another (spec, events)", len(rows))
        if not isinstance(row.get("caught"), bool):
            return _ranking_refusal(
                f"row {pos} has no boolean caught status — relevance unmeasurable",
                len(rows))
    ids = [row["mutation_id"] for row in rows]
    if published_ids is None:
        published = ids
    else:
        published = [str(x) for x in published_ids]
        if sorted(published) != sorted(ids):
            return _ranking_refusal(
                "published ranking is not a permutation of the audited rows (missing, "
                "duplicated, or foreign mutation_ids) — refused, not scored", len(rows))
    caught_by_id = {row["mutation_id"]: bool(row["caught"]) for row in rows}
    relevances = [1 if caught_by_id[i] else 0 for i in published]
    value = ndcg(relevances)
    if value is None:
        return _ranking_refusal(
            "IDCG is zero (the audit killed nothing) — nDCG undefined; no ranking of "
            "kills exists to grade", len(rows))
    return {"ok": True, "reason": None, "ndcg": value,
            "order_sensitive": 0 in relevances and 1 in relevances, "n": len(rows)}
