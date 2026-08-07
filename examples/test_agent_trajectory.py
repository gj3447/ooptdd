"""Agent-trajectory gates — the absorbed DeepEval/Phoenix vocabulary, RED and GREEN.

Run it:  pytest examples/test_agent_trajectory.py -s

Zero infrastructure (memory backend). Each pair shows a bug an ordinary
return-value test cannot see, because the evidence is the *arrived* trace:

  * the agent claims "done" but the expected tool call never landed   -> RED
  * the agent called a forbidden tool                                  -> RED
  * the agent called a normally-valid tool with a prohibited arg shape -> RED
  * the run blew its token budget                                      -> RED
"""

from __future__ import annotations

from ooptdd_trajectory import ooptdd_checks

from examples.agent_gen_ai import run_agent
from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.bootstrap import compose_runtime


def _runtime():
    return compose_runtime(
        project={"extensions": ["trajectory"]},
        environment={},
        extension_providers={"trajectory": ooptdd_checks},
    ).activate_extensions()


def _gate(cid: str, expect: list) -> dict:
    return {"cid": cid, "expect": expect}


def test_green_expected_tools_arrived_within_budget():
    reset()
    b = MemoryBackend()
    cid = "traj-green"
    run_agent(b, cid, tools=["search", "read_file"])
    res = _runtime().evaluate(
        b,
        _gate(
            cid,
            [
                {"tool_calls": {"expected": ["search", "read_file"]}},  # subset recall
                {
                    "tool_calls": {
                        "expected": ["search", "read_file"],  # and in order
                        "match": "ordered",
                    }
                },
                {"forbidden_tools": ["shell_exec"]},
                {"aggregate": {"fn": "sum", "attr": "gen_ai.usage.output_tokens", "target": 1000}},
            ],
        ),
    )
    assert res["ok"]
    reset()


def test_red_claimed_tool_never_landed():
    """The founding failure mode, agent-shaped: `run_agent` returns status=ok either
    way — only the arrived trace shows `read_file` was never actually called."""
    reset()
    b = MemoryBackend()
    cid = "traj-red-missing"
    out = run_agent(b, cid, tools=["search"])  # read_file skipped
    assert out["status"] == "ok"  # the self-report is green — and worthless
    res = _runtime().evaluate(
        b,
        _gate(
            cid,
            [
                {"tool_calls": {"expected": ["search", "read_file"]}},
            ],
        ),
    )
    assert not res["ok"]
    [chk] = res["checks"]
    assert chk["missing"] == ["read_file"] and chk["score"] == 0.5
    reset()


def test_red_forbidden_tool_arrived():
    reset()
    b = MemoryBackend()
    cid = "traj-red-forbidden"
    run_agent(b, cid, tools=["search", "shell_exec"])
    res = _runtime().evaluate(b, _gate(cid, [{"forbidden_tools": ["shell_exec"]}]))
    assert not res["ok"]
    assert res["checks"][0]["offenders"] == ["shell_exec"]
    reset()


def test_red_forbidden_tool_argument_shape_arrived():
    reset()
    b = MemoryBackend()
    cid = "traj-red-forbidden-args"
    b.ship(
        [
            {
                "event": "gen_ai.execute_tool",
                "gen_ai.tool.name": "shell_exec",
                "gen_ai.tool.call.arguments": {"command": "rm -rf build"},
                "cid": cid,
                "correlation_id": cid,
                "cycle_id": cid,
            }
        ]
    )
    res = _runtime().evaluate(
        b,
        _gate(
            cid,
            [
                {
                    "forbidden_tool_calls": [
                        {
                            "name": "shell_exec",
                            "args": {"command": {"non_empty": True, "contains_any": ["rm -rf"]}},
                        }
                    ]
                }
            ],
        ),
    )
    assert not res["ok"]
    assert res["checks"][0]["offenders"] == ["shell_exec"]
    reset()


def test_red_token_budget_blown():
    reset()
    b = MemoryBackend()
    cid = "traj-red-budget"
    run_agent(b, cid, tools=["search"])  # emits 64 output tokens
    res = _runtime().evaluate(
        b,
        _gate(
            cid,
            [
                {"aggregate": {"fn": "sum", "attr": "gen_ai.usage.output_tokens", "target": 50}},
            ],
        ),
    )
    assert not res["ok"]
    reset()
