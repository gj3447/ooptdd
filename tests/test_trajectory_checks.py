"""tool_calls / forbidden_tools — the deterministic agent-trajectory predicates.

Pins the three absorbed matching modes (exact / subset / ordered), Jaccard argument
credit, arrival-grounding (self-reported calls that never landed score zero), the
honesty accounting (charged / strength), and the negative wing.
"""
import ooptdd.engine.gate as gate

CID = "traj-cid"


def _tool(name, args=None, **extra):
    ev = {"event": "gen_ai.execute_tool", "gen_ai.tool.name": name,
          "cid": CID, "_timestamp": extra.pop("ts", 0)}
    if args is not None:
        ev["gen_ai.tool.call.arguments"] = args
    ev.update(extra)
    return ev


def _eval(expect, events):
    return gate.evaluate_events({"cid": CID, "expect": expect}, events,
                                reachable=True, cid=CID)


def _chk(res):
    return res["checks"][0]


# ── subset (default): order-independent recall ─────────────────────────────────


def test_subset_all_expected_arrived_any_order_green():
    res = _eval([{"tool_calls": {"expected": ["search", "read_file"]}}],
                [_tool("read_file", ts=1), _tool("search", ts=2), _tool("extra", ts=3)])
    chk = _chk(res)
    assert res["ok"] and chk["passed"] and chk["score"] == 1.0
    assert chk["charged"] is True
    assert chk["strength"] == "value-pinned"


def test_subset_missing_tool_scores_recall_and_fails_default_target():
    res = _eval([{"tool_calls": {"expected": ["search", "read_file"]}}],
                [_tool("search")])
    chk = _chk(res)
    assert not res["ok"] and not chk["passed"]
    assert chk["score"] == 0.5 and chk["missing"] == ["read_file"]


def test_subset_threshold_target_passes_partial():
    res = _eval([{"tool_calls": {"expected": ["search", "read_file"], "target": 0.5}}],
                [_tool("search")])
    assert res["ok"] and _chk(res)["passed"]


def test_no_arrivals_scores_zero_uncharged():
    """The ooptdd twist: a claimed-but-never-landed call is worth nothing."""
    res = _eval([{"tool_calls": {"expected": ["search"]}}], [])
    chk = _chk(res)
    assert not chk["passed"] and chk["score"] == 0.0 and chk["charged"] is False


# ── ordered: weighted LCS ──────────────────────────────────────────────────────


def test_ordered_swap_loses_the_out_of_order_call():
    res = _eval([{"tool_calls": {"expected": ["plan", "search", "write"],
                                 "match": "ordered"}}],
                [_tool("search", ts=1), _tool("plan", ts=2), _tool("write", ts=3)])
    chk = _chk(res)
    assert not chk["passed"]
    assert abs(chk["score"] - 2 / 3) < 1e-3  # LCS keeps 2 of 3 (score rounds to 4dp)
    assert chk["missing"] == []  # arrived, just out of order — not "missing"


def test_ordered_tolerates_interleaved_extras():
    res = _eval([{"tool_calls": {"expected": ["plan", "write"], "match": "ordered"}}],
                [_tool("plan", ts=1), _tool("noise", ts=2), _tool("write", ts=3)])
    assert _chk(res)["passed"]


# ── exact: positional, extras fail ─────────────────────────────────────────────


def test_exact_extra_call_is_red():
    res = _eval([{"tool_calls": {"expected": ["search"], "match": "exact"}}],
                [_tool("search"), _tool("search")])
    assert _chk(res)["score"] == 0.0


def test_exact_same_sequence_green():
    res = _eval([{"tool_calls": {"expected": ["a", "b"], "match": "exact"}}],
                [_tool("a", ts=1), _tool("b", ts=2)])
    assert _chk(res)["passed"]


# ── argument credit ────────────────────────────────────────────────────────────


def test_args_jaccard_partial_credit_subset():
    res = _eval([{"tool_calls": {
        "expected": [{"name": "search", "args": {"q": "cats", "limit": 5}}],
        "compare": ["name", "args"], "target": 0.5}}],
        [_tool("search", args={"q": "cats", "limit": 99})])
    chk = _chk(res)
    assert chk["passed"] and abs(chk["score"] - 0.5) < 1e-9  # 1 of 2 keys match


def test_args_exact_mode_binarizes():
    res = _eval([{"tool_calls": {
        "expected": [{"name": "search", "args": {"q": "cats"}}],
        "match": "exact", "compare": ["name", "args"]}}],
        [_tool("search", args={"q": "dogs"})])
    assert _chk(res)["score"] == 0.0


def test_args_arrive_as_json_string():
    res = _eval([{"tool_calls": {
        "expected": [{"name": "search", "args": {"q": "cats"}}],
        "compare": ["name", "args"]}}],
        [_tool("search", args='{"q": "cats"}')])
    assert _chk(res)["passed"]


def test_nested_args_recurse():
    res = _eval([{"tool_calls": {
        "expected": [{"name": "cfg", "args": {"opts": {"a": 1, "b": 2}}}],
        "compare": ["name", "args"], "target": 0.4}}],
        [_tool("cfg", args={"opts": {"a": 1, "b": 3}})])
    assert _chk(res)["passed"]  # nested credit: 0.5 of the single key -> 0.5


# ── matcher vocabulary (Phoenix pxi absorption) ────────────────────────────────


def test_matcher_contains_any_and_absent():
    exp = [{"name": "search", "args": {"q": {"contains_any": ["cats", "dogs"]},
                                       "debug": {"absent": True}}}]
    ok = _eval([{"tool_calls": {"expected": exp, "compare": ["name", "args"]}}],
               [_tool("search", args={"q": "I like cats", "limit": 5})])
    assert _chk(ok)["passed"]
    red = _eval([{"tool_calls": {"expected": exp, "compare": ["name", "args"]}}],
                [_tool("search", args={"q": "I like cats", "debug": True})])
    assert not _chk(red)["passed"]  # forbidden key arrived -> binary zero


def test_matcher_mode_is_binary_not_jaccard():
    exp = [{"name": "search", "args": {"q": {"equals": "cats"}, "limit": 5}}]
    res = _eval([{"tool_calls": {"expected": exp, "compare": ["name", "args"],
                                 "target": 0.5}}],
                [_tool("search", args={"q": "cats", "limit": 99})])
    chk = _chk(res)
    assert chk["score"] == 0.0 and not chk["passed"]  # literal limit mismatch kills it


def test_matcher_non_empty_and_has_keys():
    exp = [{"name": "cfg", "args": {"opts": {"has_keys": ["a", "b"]},
                                    "tag": {"non_empty": True}}}]
    res = _eval([{"tool_calls": {"expected": exp, "compare": ["name", "args"]}}],
                [_tool("cfg", args={"opts": {"a": 1, "b": 2, "c": 3}, "tag": "x"})])
    assert _chk(res)["passed"]


# ── grill regressions (2026-07-22 adversarial review F1-F8) ────────────────────


def test_f1_unparseable_args_fail_closed_in_matcher_mode():
    """Corrupt JSON args ARRIVED — an `absent:` matcher must not pass by pretending
    the args were empty (fail-open was the F1 false-GREEN vector)."""
    exp = [{"name": "search", "args": {"debug": {"absent": True}}}]
    res = _eval([{"tool_calls": {"expected": exp, "compare": ["name", "args"]}}],
                [_tool("search", args='{"debug": tr')])  # truncated JSON
    assert not _chk(res)["passed"]


def test_f1_no_args_at_all_still_satisfies_absent():
    """A call that genuinely carried no args: `absent` legitimately holds."""
    exp = [{"name": "search", "args": {"debug": {"absent": True}}}]
    res = _eval([{"tool_calls": {"expected": exp, "compare": ["name", "args"]}}],
                [_tool("search")])
    assert _chk(res)["passed"]


def test_f2_scalar_want_for_contains_matchers_is_a_loud_spec_error():
    import pytest
    exp = [{"name": "search", "args": {"q": {"contains_any": "cats"}}}]
    with pytest.raises(ValueError, match="per-CHARACTER"):
        _eval([{"tool_calls": {"expected": exp, "compare": ["name", "args"]}}],
              [_tool("search", args={"q": "zebra"})])


def test_f3_not_contains_passes_when_key_absent():
    exp = [{"name": "run", "args": {"cmd": {"not_contains": ["rm -rf"]}}}]
    ok = _eval([{"tool_calls": {"expected": exp, "compare": ["name", "args"]}}],
               [_tool("run")])  # no cmd arg at all: nothing to prohibit
    assert _chk(ok)["passed"]
    red = _eval([{"tool_calls": {"expected": exp, "compare": ["name", "args"]}}],
                [_tool("run", args={"cmd": "rm -rf /"})])
    assert not _chk(red)["passed"]


def test_f4_empty_expected_and_empty_forbidden_are_spec_errors():
    import pytest
    with pytest.raises(ValueError, match="non-empty"):
        _eval([{"tool_calls": {"expected": []}}], [_tool("a")])
    with pytest.raises(ValueError, match="at least one tool"):
        _eval([{"forbidden_tools": []}], [_tool("a")])


def test_f8_nested_matcher_is_a_loud_spec_error_not_silent_literal():
    import pytest
    exp = [{"name": "cfg", "args": {"opts": {"q": {"contains_any": ["x"]}}}}]
    with pytest.raises(ValueError, match="top level"):
        _eval([{"tool_calls": {"expected": exp, "compare": ["name", "args"]}}],
              [_tool("cfg", args={"opts": {"q": "x"}})])


def test_f8_matcher_inside_a_list_value_is_also_loud():
    """grill F8-list: a matcher buried in a LIST value was silently scored as a literal."""
    import pytest
    exp = [{"name": "f", "args": {"tags": [{"contains_any": ["secret"]}]}}]
    with pytest.raises(ValueError, match="top level"):
        _eval([{"tool_calls": {"expected": exp, "compare": ["name", "args"]}}],
              [_tool("f", args={"tags": ["secret"]})])


def test_aggregate_rejects_infinity_and_nan_strings():
    """grill MEDIUM-4: float('inf')/'nan' strings must not count as evidence (false-GREEN
    on a max-budget, invalid bare-NaN JSON output)."""
    import json
    res = _eval([{"aggregate": {"fn": "max", "attr": "gen_ai.usage.output_tokens",
                                "event": "gen_ai.chat", "target": 100}}],
                [{"event": "gen_ai.chat", "gen_ai.usage.output_tokens": "Infinity",
                  "cid": CID, "_timestamp": 0}])
    chk = _chk(res)
    assert chk["n"] == 0 and chk["value"] is None and not chk["passed"]
    json.dumps(res, allow_nan=False)  # would raise if a bare Infinity/NaN slipped through


def test_compare_as_comma_string_is_normalized():
    res = _eval([{"tool_calls": {
        "expected": [{"name": "search", "args": {"q": "cats"}}],
        "compare": "name,args"}}],
        [_tool("search", args={"q": "cats"})])
    chk = _chk(res)
    assert chk["passed"] and chk["compare"] == ["name", "args"]


def test_unknown_op_is_a_clean_valueerror():
    import pytest
    with pytest.raises(ValueError, match="unknown op"):
        _eval([{"tool_calls": {"expected": ["a"], "op": "=<"}}], [_tool("a")])


def test_f7_trajectory_gate_stream_coverage_names_its_events():
    """A trajectory-only gate must not report its own scored events as unasserted."""
    res = _eval([{"tool_calls": {"expected": ["search"]}}], [_tool("search")])
    scope = res.get("scope", {})
    unasserted = scope.get("unasserted_observed", [])
    assert "gen_ai.execute_tool" not in unasserted


def test_f6_mutation_excludes_trajectory_predicates_from_count_mutants():
    from ooptdd.mutation import derive_mutations
    spec = {"cid": CID, "expect": [{"forbidden_tools": ["rm"]},
                                   {"tool_calls": {"expected": ["a"]}},
                                   {"aggregate": {"fn": "sum", "attr": "x", "target": 1}}]}
    muts = derive_mutations([_tool("a")], spec)
    names = [m[0] if isinstance(m, tuple) else m.get("name", "") for m in muts]
    assert not any(str(n).startswith("drop:") for n in names), \
        f"trajectory rules must not fall through to count-rule drop mutants: {names}"


# ── aggregate: rollup budgets (Phoenix cumulative-rollup absorption) ───────────


def _chat(tokens, ts=0):
    return {"event": "gen_ai.chat", "gen_ai.usage.output_tokens": tokens,
            "cid": CID, "_timestamp": ts}


def test_aggregate_sum_within_budget_green():
    res = _eval([{"aggregate": {"fn": "sum", "attr": "gen_ai.usage.output_tokens",
                                "target": 100}}],
                [_chat(40), _chat(50)])
    chk = _chk(res)
    assert chk["passed"] and chk["value"] == 90 and chk["charged"] is True
    assert chk["strength"] == "threshold"


def test_aggregate_sum_over_budget_red():
    res = _eval([{"aggregate": {"fn": "sum", "attr": "gen_ai.usage.output_tokens",
                                "target": 100}}],
                [_chat(80), _chat(50)])
    assert not _chk(res)["passed"]


def test_aggregate_max_and_event_filter():
    res = _eval([{"aggregate": {"fn": "max", "attr": "gen_ai.usage.output_tokens",
                                "event": "gen_ai.chat", "target": 60}}],
                [_chat(40), _chat(55),
                 {"event": "other", "gen_ai.usage.output_tokens": 999,
                  "cid": CID, "_timestamp": 0}])
    chk = _chk(res)
    assert chk["passed"] and chk["value"] == 55 and chk["n"] == 2


def test_aggregate_no_values_sum_vacuous_but_uncharged_max_inconclusive_red():
    res = _eval([{"aggregate": {"fn": "sum", "attr": "gen_ai.usage.output_tokens",
                                "target": 100}}], [])
    chk = _chk(res)
    assert chk["passed"] and chk["charged"] is False  # vacuous budget, no evidence
    res2 = _eval([{"aggregate": {"fn": "max", "attr": "gen_ai.usage.output_tokens",
                                 "target": 100}}], [])
    assert not _chk(res2)["passed"]  # max over nothing is not a pass


def test_aggregate_ignores_non_numeric_and_bool():
    res = _eval([{"aggregate": {"fn": "sum", "attr": "gen_ai.usage.output_tokens",
                                "target": 10}}],
                [_chat(7), _chat("lots"), _chat(True)])
    chk = _chk(res)
    assert chk["value"] == 7 and chk["n"] == 1


# ── custom event/attr mapping ──────────────────────────────────────────────────


def test_custom_event_and_name_attr():
    res = _eval([{"tool_calls": {"expected": ["probe"], "event": "mcp.call",
                                 "name_attr": "mcp.tool"}}],
                [{"event": "mcp.call", "mcp.tool": "probe", "cid": CID, "_timestamp": 0}])
    assert _chk(res)["passed"]


# ── forbidden_tools: the negative wing ─────────────────────────────────────────


def test_forbidden_tool_arrival_is_red_and_charged():
    res = _eval([{"forbidden_tools": ["delete_db"]}],
                [_tool("search"), _tool("delete_db")])
    chk = _chk(res)
    assert not res["ok"] and chk["offenders"] == ["delete_db"]
    assert chk["charged"] is True and chk["strength"] == "forbid"


def test_forbidden_absent_green_uncharged():
    res = _eval([{"forbidden_tools": ["delete_db"]}], [_tool("search")])
    chk = _chk(res)
    assert res["ok"] and chk["passed"] and chk["charged"] is False


def test_forbidden_unreachable_store_never_green():
    res = gate.evaluate_events({"cid": CID, "expect": [{"forbidden_tools": ["x"]}]},
                               [], reachable=False, cid=CID)
    assert not res["ok"]  # absence claimed on an unreachable store is not a pass
