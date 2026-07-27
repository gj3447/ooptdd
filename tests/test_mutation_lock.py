"""Winner's-curse lock (POST_TIER1_ARC front A3): the mutation threshold is committed
before the run and cannot be re-picked after peeking at the score.

The lock file pins (gate spec sha256, min_score). ``ooptdd mutate --lock`` refuses to
grade — exit 2, a setup refusal, never a measured verdict — when the spec moved after
locking or when a CLI ``--min-score`` tries to override the locked threshold. A score
below the locked threshold stays exit 1: the honest RED the front expects.
"""
import hashlib
import json

from ooptdd.cli import main
from ooptdd.mutation import LOCK_SCHEMA, verify_mutation_lock

SPEC_TEXT = ("cid: mut-lock\n"
             "expect:\n"
             "  - present: [{event: deploy}]\n")
EVENTS = [{"event": "deploy"}]


def _lock_for(text: str, min_score: float) -> dict:
    return {"schema": LOCK_SCHEMA,
            "gate_spec_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "min_score": min_score}


# ── verifier unit surface ──────────────────────────────────────────────────────────────

def test_lock_binds_when_spec_and_threshold_match():
    got, reason = verify_mutation_lock(SPEC_TEXT.encode(), _lock_for(SPEC_TEXT, 0.8))
    assert (got, reason) == (0.8, None)


def test_matching_cli_threshold_is_not_a_repick():
    got, reason = verify_mutation_lock(SPEC_TEXT.encode(), _lock_for(SPEC_TEXT, 0.8), 0.8)
    assert (got, reason) == (0.8, None)


def test_conflicting_cli_threshold_is_refused():
    got, reason = verify_mutation_lock(SPEC_TEXT.encode(), _lock_for(SPEC_TEXT, 0.8), 0.5)
    assert got is None and "re-picked" in reason


def test_moved_spec_is_refused():
    got, reason = verify_mutation_lock(b"tampered: true\n", _lock_for(SPEC_TEXT, 0.8))
    assert got is None and "does not match the lock" in reason


def test_unknown_schema_and_missing_fields_are_refused():
    assert verify_mutation_lock(SPEC_TEXT.encode(), {"schema": "nope"})[0] is None
    assert verify_mutation_lock(
        SPEC_TEXT.encode(), {"schema": LOCK_SCHEMA, "min_score": "high"})[0] is None


# ── CLI surface ────────────────────────────────────────────────────────────────────────

def _materialize(tmp_path, spec_text=SPEC_TEXT, min_score=0.8):
    spec = tmp_path / "gate.yaml"
    spec.write_text(spec_text, encoding="utf-8")
    ev = tmp_path / "events.json"
    ev.write_text(json.dumps(EVENTS), encoding="utf-8")
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps(_lock_for(spec_text, min_score)), encoding="utf-8")
    return spec, ev, lock


def test_cli_locked_run_grades_at_the_locked_threshold(tmp_path, capsys):
    spec, ev, lock = _materialize(tmp_path, min_score=0.8)
    rc = main(["mutate", str(spec), "--events", str(ev), "--lock", str(lock), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["lock"]["min_score"] == 0.8      # audit trail carries the lock
    assert rc in (0, 1)                          # graded — measured verdict either way
    assert rc == (0 if out["score"] >= 0.8 else 1)


def test_cli_refuses_threshold_repick_after_lock(tmp_path, capsys):
    spec, ev, lock = _materialize(tmp_path, min_score=0.8)
    rc = main(["mutate", str(spec), "--events", str(ev), "--lock", str(lock),
               "--min-score", "0.1"])
    err = capsys.readouterr().err
    assert rc == 2 and "LOCK REFUSED" in err and "re-picked" in err


def test_cli_refuses_spec_that_moved_after_lock(tmp_path, capsys):
    spec, ev, lock = _materialize(tmp_path)
    spec.write_text(SPEC_TEXT + "  - present: {where: {event: extra}}\n", encoding="utf-8")
    rc = main(["mutate", str(spec), "--events", str(ev), "--lock", str(lock)])
    err = capsys.readouterr().err
    assert rc == 2 and "does not match the lock" in err


def test_cli_score_below_locked_threshold_is_the_honest_red(tmp_path, capsys):
    # A tolerant subset-trajectory gate (target 0.5) lets every rename mutant through —
    # deterministic score 0.0. Under a locked threshold of 1.0 that is exit 1, reported
    # exactly, never renegotiated: the front's expected honest RED.
    spec_text = ("cid: mut-lock\n"
                 "expect:\n"
                 "  - tool_calls: {expected: [a, b], match: subset, target: 0.5}\n")
    spec, ev, lock = _materialize(tmp_path, spec_text=spec_text, min_score=1.0)
    ev.write_text(json.dumps([
        {"event": "gen_ai.execute_tool", "gen_ai.tool.name": "a"},
        {"event": "gen_ai.execute_tool", "gen_ai.tool.name": "b"},
    ]), encoding="utf-8")
    rc = main(["mutate", str(spec), "--events", str(ev), "--lock", str(lock), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["score"] == 0.0 and out["lock"]["min_score"] == 1.0
    assert rc == 1
