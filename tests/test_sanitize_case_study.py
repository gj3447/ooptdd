"""scripts/sanitize_case_study.py — the case-study receipt sanitizer.

Proves the three claims docs/case_study_template.md makes about it:
identity fields are stripped/hashed, verdicts and counts survive untouched,
and ``--check`` actually catches a configured term that leaked.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "sanitize_case_study", REPO / "scripts" / "sanitize_case_study.py"
)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

CID = "run-2026-07-22-abc123"
URL = "https://logs.internal.acme-corp.example:5080"


def _verify_gate_result() -> dict:
    """A realistic ``verify_gate`` verdict (shape per engine/verify.py +
    engine/gate.py evaluate_events): nested gate result with identity-bearing
    ``cid`` / ``oracle.emit_identity`` / per-check ``derived_identity``."""
    return {
        "ok": True,
        "verdict": "present",
        "gate": {
            "ok": True, "reachable": True, "complete": True, "probe_reachable": True,
            "vacuous": False, "uncorroborated": False, "unauthenticated": False,
            "dependent_store": False, "authenticated": None,
            "cid": CID,
            "checks": [
                {"event": "order_created", "count": 1, "op": ">=", "passed": True,
                 "optional": False, "pending": False, "weight": 1.0,
                 "strength": "value-pinned", "kind": "count",
                 "grounding": "derived-self", "charged": True},
                {"external": "inventory", "passed": True, "optional": False,
                 "pending": False, "weight": 1.0, "strength": "external",
                 "kind": "external", "grounding": "corroborated", "charged": True,
                 "separate_source": True, "derived_identity": URL},
            ],
            "oracle": {"gating": 2, "corroborated": 1, "derived_self": 1,
                       "single_authority": False, "enforced": False,
                       "emit_backend": "OpenObserveBackend",
                       "emit_identity": URL, "relocated": 0,
                       "signature_enforced": False, "forbid_errors": False},
            "scope": {"gating": 2, "optional": 0, "pending": 0, "total": 2,
                      "asserts_anything": True, "observed_event_types": 3,
                      "named_event_types": 1, "unasserted_observed": ["order_paid"],
                      "stream_coverage": 1 / 3, "charged": 2, "charge_ratio": 1.0,
                      "uncharged": []},
            "optional_failed": [], "pending_failed": [], "pending_satisfied": [],
        },
        "reasons": [],
        "attempts": 1,
        "arrival": {"visibility_delay_ms": 0, "waited_ms": 12, "flushed": False,
                    "extended_for_visibility": False, "confirm_rounds_run": 0},
    }


def _session_finish_result() -> dict:
    """A ``session_finish`` result (engine/verify.py): the cid lives only inside
    the free-text messages, as ``cid=<value>``."""
    return {
        "shipped": 42,
        "messages": [
            f"42 test traces shipped (cid={CID})",
            "OK arrival confirmed (session 42/42, outcomes=42, 1 attempt)",
        ],
        "fail_build": False,
    }


def test_sensitive_fields_stripped_or_hashed():
    out = sc.sanitize(_verify_gate_result(), sensitive=["acme-corp"])
    text = json.dumps(out)
    assert CID not in text
    assert URL not in text
    assert "acme-corp" not in text.lower()
    assert out["gate"]["cid"].startswith("anon-")
    assert out["gate"]["oracle"]["emit_identity"].startswith("anon-")
    assert out["gate"]["checks"][1]["derived_identity"].startswith("anon-")
    # equal originals -> equal tokens: the "probe re-read the emit endpoint"
    # relation stays checkable after anonymization
    assert (out["gate"]["oracle"]["emit_identity"]
            == out["gate"]["checks"][1]["derived_identity"])
    # deterministic: same input + config -> identical output
    assert out == sc.sanitize(_verify_gate_result(), sensitive=["acme-corp"])


def test_cid_scrubbed_inside_free_text_messages():
    out = sc.sanitize(_session_finish_result())
    assert CID not in json.dumps(out)
    assert out["messages"][0].startswith("42 test traces shipped (cid=anon-")


def test_verdict_and_counts_preserved():
    out = sc.sanitize(_verify_gate_result(), sensitive=["acme-corp"])
    assert out["ok"] is True and out["verdict"] == "present" and out["attempts"] == 1
    g = out["gate"]
    assert g["ok"] is True and g["reachable"] is True
    assert g["oracle"]["gating"] == 2 and g["oracle"]["corroborated"] == 1
    assert g["oracle"]["emit_backend"] == "OpenObserveBackend"  # class name, not a host
    assert g["scope"]["total"] == 2 and g["scope"]["charge_ratio"] == 1.0
    assert g["checks"][0]["passed"] is True and g["checks"][0]["kind"] == "count"
    sf = sc.sanitize(_session_finish_result())
    assert sf["shipped"] == 42 and sf["fail_build"] is False


def test_check_mode_catches_a_leak(tmp_path):
    leaky = tmp_path / "leaky.json"
    leaky.write_text(json.dumps({"note": f"judged on {URL}"}), encoding="utf-8")
    assert sc.main([str(leaky), "--check", "--sensitive", "acme-corp"]) == 1
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({"note": "judged on anon-abc123def456"}),
                     encoding="utf-8")
    assert sc.main([str(clean), "--check", "--sensitive", "acme-corp"]) == 0
    # zero configured terms: the check can never fail -> vacuous, exit 2 not 0
    assert sc.main([str(leaky), "--check"]) == 2


def test_end_to_end_sanitize_then_check_clean(tmp_path):
    src = tmp_path / "verdict.json"
    src.write_text(json.dumps(_verify_gate_result()), encoding="utf-8")
    dst = tmp_path / "case_study_receipt.json"
    assert sc.main([str(src), "-o", str(dst), "--sensitive", "acme-corp"]) == 0
    receipt = json.loads(dst.read_text(encoding="utf-8"))
    assert receipt["_sanitizer"]["tool"] == "scripts/sanitize_case_study.py"
    assert sc.main([str(dst), "--check", "--sensitive", "acme-corp",
                    "--sensitive", CID]) == 0
