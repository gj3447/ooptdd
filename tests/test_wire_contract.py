"""The event envelope needs a versioned, machine-readable wire contract (audit gap-09).

The envelope was described only in prose; every shipped record carried no spec_version, so
out-of-process emitters (p333's Rust, omd) re-implemented it by imitation and drifted, and
`ooptdd schema` emitted prose, not a schema document. This pins the contract: a stamped
spec_version on every builder record, an in-package ENVELOPE_SCHEMA (the SSOT) mirrored on disk
for external consumers, and a CLI that emits the actual schema.
"""
import json
from pathlib import Path

import ooptdd.cli as cli
from ooptdd.domain.model import (
    ENVELOPE_SCHEMA,
    ENVELOPE_SPEC_VERSION,
    build_outcome_records,
    build_session_start,
    correlation_keys,
)
from ooptdd.domain.ontology import EventType

_SCHEMA_FILE = Path(__file__).resolve().parents[1] / "docs" / "schema" / "envelope.schema.json"


def _validate(schema: dict, inst) -> list[str]:
    """Tiny JSON-Schema-2020-12 subset validator (type object|string, required, properties with
    const/enum/type). Returns violation strings; [] means valid. Stdlib only — jsonschema is not
    a dependency."""
    errs: list[str] = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(inst, dict):
            return [f"expected object, got {type(inst).__name__}"]
        for req in schema.get("required", []):
            if req not in inst:
                errs.append(f"missing required '{req}'")
        for k, sub in (schema.get("properties") or {}).items():
            if k in inst:
                errs += [f"{k}: {e}" for e in _validate(sub, inst[k])]
    elif t == "string" and not isinstance(inst, str):
        errs.append(f"expected string, got {type(inst).__name__}")
    if "const" in schema and inst != schema["const"]:
        errs.append(f"const mismatch: {inst!r} != {schema['const']!r}")
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{inst!r} not in enum {schema['enum']}")
    return errs


def _records() -> list[dict]:
    recs = build_outcome_records(
        [{"nodeid": "t::a", "outcome": "passed", "when": "call", "duration": 0.1}], "wc-cid")
    recs.append(build_session_start("wc-cid"))
    return recs


def _session() -> dict:
    return next(r for r in _records() if r["event"] == "test_session")


# ── GUARD 1: trap-guard (green before AND after) — the wrong-place fix must not pass ──
def test_correlation_keys_stays_exactly_three_aliases():
    """spec_version must NOT be stamped into correlation_keys (it would break every consumer of
    that primitive and this exact-dict contract); it belongs in the record builders."""
    assert correlation_keys("c") == {"cid": "c", "correlation_id": "c", "cycle_id": "c"}


# ── GUARD 2: the fix flips red -> green ──────────────────────────────────────────────
def test_every_builder_record_carries_spec_version():
    recs = _records()
    assert {r["event"] for r in recs} == {"test_outcome", "test_session", "session_start"}
    for r in recs:
        assert r.get("spec_version") == ENVELOPE_SPEC_VERSION


def test_cli_schema_envelope_emits_the_schema_doc(capsys):
    rc = cli.main(["schema", "envelope", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == ENVELOPE_SCHEMA
    assert out["properties"]["spec_version"]["const"] == ENVELOPE_SPEC_VERSION
    assert "$schema" in out and "doc" not in out  # an actual schema, not a prose cheat-sheet


def test_ondisk_schema_mirrors_the_inpackage_constant():
    assert json.loads(_SCHEMA_FILE.read_text()) == ENVELOPE_SCHEMA


# ── GUARD 3: no-false-alarm / revert-proof ───────────────────────────────────────────
def test_shipped_session_validates_clean():
    """Kills a reject-all fake schema/validator."""
    assert _validate(ENVELOPE_SCHEMA, _session()) == []


def test_schema_rejects_a_missing_spec_version():
    """Kills an accept-all {'type':'object'} schema."""
    bad = _session()
    bad.pop("spec_version")
    assert _validate(ENVELOPE_SCHEMA, bad) != []


def test_schema_rejects_a_drifted_spec_version():
    """Kills a schema whose const drifted from the shipped value."""
    bad = _session()
    bad["spec_version"] = "9.9.9"
    assert _validate(ENVELOPE_SCHEMA, bad) != []


def test_closed_ontology_does_not_flag_spec_version():
    """The companion ontology edit: a closed EventType must treat spec_version as a carrier key,
    not unexpected payload drift. Declaring the real payload keys leaves only envelope keys as
    'extra' — all must be recognized carriers, so reverting the ENVELOPE_KEYS edit reds this."""
    et = EventType(name="test_session", required=["total", "passed", "failed", "skipped"],
                   additional_properties=False)
    errs = et.validate(_session())
    assert errs == [], errs
    assert not any("spec_version" in e for e in errs)
