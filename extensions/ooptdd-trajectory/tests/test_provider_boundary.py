from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ooptdd import evaluate_events
from ooptdd.bootstrap import compose_runtime
from ooptdd.engine import gate

import ooptdd_trajectory


def test_provider_import_has_no_global_registry_side_effect():
    assert not set(ooptdd_trajectory.ooptdd_checks()) & set(gate.core_check_registry())


def test_runtime_activates_only_the_explicitly_injected_provider():
    base = compose_runtime(project={}, environment={}, extension_providers={})
    runtime = compose_runtime(
        project={"extensions": ["trajectory"]},
        environment={},
        extension_providers={"trajectory": ooptdd_trajectory.ooptdd_checks},
    ).activate_extensions()
    expected = {
        "trajectory": "ordered",
        "tool_calls": "value-pinned",
        "forbidden_tools": "forbid",
        "forbidden_tool_calls": "forbid",
    }
    strengths = gate.check_strengths(runtime.check_registry)
    assert all(strengths[key] == value for key, value in expected.items())
    assert not set(expected) & set(base.check_registry)
    result = evaluate_events(
        {"cid": "c", "expect": [{"trajectory": ["plan", "act"]}]},
        [{"event": "plan", "_timestamp": 1}, {"event": "act", "_timestamp": 2}],
        reachable=True,
        environ={},
        registry=runtime.check_registry,
    )
    assert result["ok"]


def test_import_is_inert_in_a_fresh_interpreter():
    source = Path(__file__).resolve().parents[1] / "src"
    base = Path(__file__).resolve().parents[3] / "src"
    code = f"""
import sys
sys.path[:0] = [{str(source)!r}, {str(base)!r}]
import ooptdd_trajectory
from ooptdd.engine import gate
assert not set(ooptdd_trajectory.ooptdd_checks()) & set(gate.core_check_registry())
"""
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
