"""The generic CLI is a stateless wrapper over domain-neutral library operations."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from ooptdd.backends.jsonl import JsonlBackend
from ooptdd.cli import build_parser, main
from ooptdd.domain.model import sign_chain


def _spec_file(tmp_path, body: str) -> str:
    p = tmp_path / "spec.yaml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def _jsonl_backend(tmp_path, monkeypatch) -> JsonlBackend:
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("OOPTDD_JSONL_PATH", str(path))
    return JsonlBackend(path=str(path))


def test_generic_parser_has_no_specialized_commands_or_imports():
    parser = build_parser()
    help_text = parser.format_help().lower()
    assert not any(word in help_text for word in ("pytest", "mutation", "trajectory", "genai"))
    script = """
import sys
import ooptdd.cli
ooptdd.cli.build_parser()
blocked = ('ooptdd.adapters.pytest', 'ooptdd_mutation',
           'ooptdd_trajectory', 'ooptdd_genai')
raise SystemExit(any(name in sys.modules for name in blocked))
"""
    completed = subprocess.run([sys.executable, "-c", script], check=False)
    assert completed.returncode == 0


# ── stateless single-shot commands ─────────────────────────────────────────────
def test_version_and_schema(capsys):
    assert main(["version"]) == 0
    assert main(["schema", "gate"]) == 0
    assert "expect:" in capsys.readouterr().out
    assert main(["schema", "ontology"]) == 0
    assert "event_types" in capsys.readouterr().out


def test_backends_list_and_doctor(capsys):
    assert main(["backends", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "memory" in payload["backends"] and "openobserve" in payload["backends"]
    assert main(["backends", "doctor", "--backend", "memory", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["queryable"] is True and info["reachable"] is True


# ── gate / verify --gate over the memory store ─────────────────────────────────
def test_gate_satisfied_and_violated(tmp_path, capsys, monkeypatch):
    backend = _jsonl_backend(tmp_path, monkeypatch)
    spec = _spec_file(tmp_path, "cid: c1\nexpect:\n  - {event: a, op: '>=', count: 1}\n")
    assert main(["gate", spec, "--backend", "jsonl"]) == 1
    capsys.readouterr()
    backend.ship([{"cid": "c1", "event": "a"}])
    assert main(["gate", spec, "--backend", "jsonl"]) == 0


@pytest.mark.parametrize("cmd", ["gate", "monitor"])
def test_missing_cid_is_a_clean_error_not_a_traceback(tmp_path, capsys, cmd):
    # A spec with no `cid:` and no OOPTDD_CID (hermetic) must NOT dump an uncaught ValueError
    # traceback — it is a config/usage error, so it prints a clean one-line message and exits 2
    # (the INFRA/usage rung), like the rest of the CLI.
    spec = _spec_file(tmp_path, "service: s\nexpect:\n  - {event: a, op: '==', count: 1}\n")
    code = main([cmd, spec])
    assert code == 2
    err = capsys.readouterr().err
    assert "cid" in err.lower()
    assert "Traceback" not in err


def test_verify_gate_flag_for_arbitrary_events(tmp_path, capsys, monkeypatch):
    backend = _jsonl_backend(tmp_path, monkeypatch)
    spec = _spec_file(tmp_path, "expect:\n  - {event: cycle, op: '>=', count: 1}\n")
    backend.ship([{"cid": "run9", "event": "cycle"}])
    assert main(["verify", "run9", "--gate", spec, "--backend", "jsonl", "--retries", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "present"


# ── can-i-deploy (Pact-style multi-gate) ───────────────────────────────────────
def test_can_i_deploy(tmp_path, capsys):
    del tmp_path, capsys
    assert "can-i-deploy" not in build_parser().format_help()


# ── mutate (gate discriminating power) ─────────────────────────────────────────
# ── ontology check / compat ────────────────────────────────────────────────────
def test_ontology_check_and_compat(tmp_path, capsys):
    onto = tmp_path / "o.yaml"
    onto.write_text("event_types:\n  pay:\n    required: [amount]\n", encoding="utf-8")
    good = tmp_path / "good.json"
    good.write_text(json.dumps([{"event": "pay", "amount": 1}]), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"event": "pay"}]), encoding="utf-8")
    assert main(["ontology", "check", str(onto), "--events", str(good)]) == 0
    assert main(["ontology", "check", str(onto), "--events", str(bad)]) == 1

    onto2 = tmp_path / "o2.yaml"
    onto2.write_text("event_types:\n  pay:\n    required: [amount, currency]\n", encoding="utf-8")
    # adding a required attr breaks backward compat
    assert main(["ontology", "compat", str(onto), str(onto2), "--mode", "backward"]) == 1


# ── verify-chain (tamper-evident receipts) ─────────────────────────────────────
def test_verify_chain_detects_tamper(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("OOPTDD_SIGNING_KEY", "k")
    chain = sign_chain(
        [{"event": "test_session", "total": 1}, {"event": "test_session", "total": 2}], "k"
    )
    recs = tmp_path / "recs.json"
    recs.write_text(json.dumps(chain), encoding="utf-8")
    assert main(["verify-chain", "--records", str(recs), "--key-env", "OOPTDD_SIGNING_KEY"]) == 0
    capsys.readouterr()
    chain[0]["total"] = 999  # tamper
    recs.write_text(json.dumps(chain), encoding="utf-8")
    assert main(["verify-chain", "--records", str(recs), "--key-env", "OOPTDD_SIGNING_KEY"]) == 1


def test_verify_chain_missing_key_is_infra(tmp_path, monkeypatch):
    monkeypatch.delenv("OOPTDD_SIGNING_KEY", raising=False)
    recs = tmp_path / "r.json"
    recs.write_text("[]", encoding="utf-8")
    assert main(["verify-chain", "--records", str(recs), "--key-env", "OOPTDD_SIGNING_KEY"]) == 2


# ── monitor surfaces the streaming verdict ─────────────────────────────────────
def test_monitor_surfaces_verdict(tmp_path, capsys, monkeypatch):
    backend = _jsonl_backend(tmp_path, monkeypatch)
    spec = _spec_file(tmp_path, "cid: m1\nexpect:\n  - {event: a, op: '>=', count: 1}\n")
    backend.ship([{"cid": "m1", "event": "a"}])
    assert main(["monitor", spec, "--backend", "jsonl", "--json"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["checks"][0]["verdict"] == "sat" and view["ok"] is True
