"""The enriched core CLI: each subcommand is a stateless wrapper over a library function,
with the LTL3 exit ladder (0 GREEN / 1 RED / 2 INFRA) and a ``--json`` machine surface.
"""
from __future__ import annotations

import json

import pytest

from ooptdd.backends.memory import MemoryBackend, reset
from ooptdd.cli import main
from ooptdd.domain.model import sign_chain


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


def _spec_file(tmp_path, body: str) -> str:
    p = tmp_path / "spec.yaml"
    p.write_text(body, encoding="utf-8")
    return str(p)


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
def test_gate_green_and_red(tmp_path, capsys):
    spec = _spec_file(tmp_path, "cid: c1\nexpect:\n  - {event: a, op: '>=', count: 1}\n")
    assert main(["gate", spec]) == 1           # nothing shipped yet -> RED
    capsys.readouterr()
    MemoryBackend().ship([{"cid": "c1", "event": "a"}])
    assert main(["gate", spec]) == 0           # now GREEN


def test_verify_gate_flag_for_arbitrary_events(tmp_path, capsys):
    spec = _spec_file(tmp_path, "expect:\n  - {event: cycle, op: '>=', count: 1}\n")
    MemoryBackend().ship([{"cid": "run9", "event": "cycle"}])
    assert main(["verify", "run9", "--gate", spec, "--retries", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "present"


# ── can-i-deploy (Pact-style multi-gate) ───────────────────────────────────────
def test_can_i_deploy(tmp_path, capsys):
    spec = _spec_file(tmp_path, "cid: dep1\nexpect:\n  - {event: ok, op: '>=', count: 1}\n")
    assert main(["can-i-deploy", spec, "--json"]) == 1   # RED blocker -> not deployable
    capsys.readouterr()
    MemoryBackend().ship([{"cid": "dep1", "event": "ok"}])
    assert main(["can-i-deploy", spec, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["deployable"] is True


# ── mutate (gate discriminating power) ─────────────────────────────────────────
def test_mutate_scores_a_gate(tmp_path, capsys):
    spec = _spec_file(tmp_path, "expect:\n  - {event: a, where: {v: 1}, op: '==', count: 1}\n")
    events = tmp_path / "ev.json"
    events.write_text(json.dumps([{"event": "a", "v": 1}]), encoding="utf-8")
    assert main(["mutate", spec, "--events", str(events), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["baseline_green"] is True and "score" in report


def test_mutate_baseline_not_green_is_infra(tmp_path, capsys):
    spec = _spec_file(tmp_path, "expect:\n  - {event: a, op: '>=', count: 5}\n")
    events = tmp_path / "ev.json"
    events.write_text(json.dumps([{"event": "a"}]), encoding="utf-8")
    assert main(["mutate", spec, "--events", str(events)]) == 2   # no baseline -> INFRA


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
    chain = sign_chain([{"event": "test_session", "total": 1},
                        {"event": "test_session", "total": 2}], "k")
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
def test_monitor_surfaces_verdict(tmp_path, capsys):
    spec = _spec_file(tmp_path, "cid: m1\nexpect:\n  - {event: a, op: '>=', count: 1}\n")
    MemoryBackend().ship([{"cid": "m1", "event": "a"}])
    assert main(["monitor", spec, "--json"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["checks"][0]["verdict"] == "sat" and view["ok"] is True
