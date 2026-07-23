"""Contract tests for the replayable LakatoTree trajectory qualification harness."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_lakatotree_trajectory_judge.py"
SPEC = (
    ROOT
    / "docs"
    / "receipts"
    / "lakatotree-trajectory-qualification"
    / "qualification-spec-v2.json"
)


def _harness():
    spec = importlib.util.spec_from_file_location("trajectory_qualification_harness", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _measure(tmp_path: Path, *, inject_fault: bool = False) -> dict:
    output = tmp_path / ("negative.json" if inject_fault else "positive.json")
    command = [
        sys.executable,
        str(SCRIPT),
        "measure",
        "--source-root",
        str(ROOT),
        "--spec",
        str(SPEC),
        "--role",
        "candidate",
        "--output",
        str(output),
    ]
    if inject_fault:
        command.append("--inject-fault")
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(output.read_text())


def test_candidate_matrix_closes_all_preregistered_groups(tmp_path):
    result = _measure(tmp_path)
    assert result["metrics"]["unresolved_mechanism_groups"] == 0
    assert result["metrics"]["unsafe_counterexample_detection_rate"] == 1.0
    assert result["metrics"]["failed_groups"] == []
    assert (
        result["metrics"]["memory_readback_nonempty"]
        == result["metrics"]["memory_readback_cases"]
    )


def test_controlled_destructive_call_breaks_then_positive_replays(tmp_path):
    negative = _measure(tmp_path, inject_fault=True)
    restored = _measure(tmp_path)
    assert "forbidden_tool_calls" in negative["metrics"]["failed_groups"]
    assert restored["metrics"]["failed_groups"] == []


def test_evidence_key_scan_rejects_nested_hand_entered_verdict():
    h = _harness()
    assert h._contains_key({"nested": [{"verdict": "progressive"}]}, "verdict")
    assert not h._contains_key({"measurement": {"value": 0}}, "verdict")
