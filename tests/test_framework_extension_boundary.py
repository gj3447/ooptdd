"""Core vocabulary stays generic; optional domains activate only by explicit import."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _isolated(program: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(program)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_clean_import_exposes_only_generic_gate_vocabulary():
    _isolated(
        """
        import ooptdd
        from ooptdd.engine import gate

        assert "aggregate" in gate.CHECK_REGISTRY
        assert not {"trajectory", "tool_calls", "forbidden_tools",
                    "forbidden_tool_calls"} & set(gate.CHECK_REGISTRY)
        loaded = __import__("sys").modules
        assert "ooptdd_trajectory" not in loaded
        assert "ooptdd_genai" not in loaded

        count = gate.evaluate_events(
            {"cid": "c", "expect": [{"event": "ready", "count": 1}]},
            [{"event": "ready"}], reachable=True, environ={})
        ordered = gate.evaluate_events(
            {"cid": "c", "expect": [{"must_order": ["a", "b"]}]},
            [{"event": "a", "_timestamp": 1}, {"event": "b", "_timestamp": 2}],
            reachable=True, environ={})
        aggregate = gate.evaluate_events(
            {"cid": "c", "expect": [{"aggregate": {"fn": "sum", "attr": "n", "target": 3}}]},
            [{"event": "a", "n": 1}, {"event": "b", "n": 2}],
            reachable=True, environ={})
        assert count["ok"] and ordered["ok"] and aggregate["ok"]
        assert aggregate["checks"][0]["strength"] == "threshold"
        """
    )


def test_unknown_structured_predicate_fails_instead_of_becoming_count():
    _isolated(
        """
        import pytest
        from ooptdd.engine.gate import evaluate_events
        from ooptdd.engine.monitor import compile_check

        with pytest.raises(ValueError, match="unknown gate predicate"):
            evaluate_events({"cid": "c", "expect": [{"trajectory": ["a", "b"]}]},
                            [], reachable=True, environ={})
        with pytest.raises(ValueError, match="unknown gate predicate"):
            compile_check({"trajectory": ["a", "b"]})
        """
    )


def test_policy_string_booleans_follow_explicit_true_false_vocabulary():
    _isolated(
        """
        import pytest
        from ooptdd.engine.gate import resolve_gate_policy

        assert resolve_gate_policy({"forbid_errors": "false"}).forbid_errors is False
        assert resolve_gate_policy({"forbid_errors": "OFF"}).forbid_errors is False
        assert resolve_gate_policy({"forbid_errors": "yes"}).forbid_errors is True
        assert resolve_gate_policy({"forbid_errors": False}).forbid_errors is False
        assert resolve_gate_policy({"forbid_errors": True}).forbid_errors is True
        with pytest.raises(ValueError, match="forbid_errors must be a boolean"):
            resolve_gate_policy({"forbid_errors": "sometimes"})
        """
    )
