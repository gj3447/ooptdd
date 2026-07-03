"""Receipt authenticity on the GATE path — the forgeable-green gap (audit 비평-1).

ooptdd already ships the anti-forgery machinery in ``domain/model.py``
(``sign_chain`` / ``verify_chain`` / ``signature_status``), and the pytest-summary
path enforces it: ``engine/verify.py`` takes ``require_signature`` and refuses a
``test_session`` summary whose ``signature_status != "valid"`` (verify.py:130,174-177),
driven by ``OOPTDD_SIGNING_KEY`` / ``OOPTDD_REQUIRE_SIGNATURE`` (plugin.py:177,190).

But that surface is NOT the one consumers gate on. Every consumer in the workspace
counts *domain* events through ``engine/gate.py::evaluate_events`` (bhgman commander
receipts, omd LTDD gates, lakatotree receipts, p333 verify). That path has NO signature
or provenance axis at all: an event is trusted purely on ``cid`` equality
(memory.py:49, jsonl.py). So any writer to the store — the code under test, a parallel
session, an attacker with append access — can MANUFACTURE a green on any positive check
by dropping in one unsigned event under the gated cid. p333's own harness shows the
asymmetry: an injected adversary is only caught in the ``absent:``/forbid direction
(run_gates.sh); a ``count >= N`` check has no such tripwire.

This file is a RED-first, revert-proof dual-guard harness. It does NOT fix the gap; it
pins it so a fix is measured, not asserted:

  * The KEYLESS-default and no-false-alarm guards must stay GREEN forever — they lock
    backward-compatibility for the 6 consumers that run keyless, and stop a "reject
    everything" pseudo-fix.
  * The forgery-detection and tampered-signature guards are ``xfail(strict=True)``:
    they are RED today (the gate path is signature-blind) and will XPASS the moment the
    axis is wired, at which point strict-xfail turns the pass into a failure that says
    "remove the marker" — the RED-first flip.

Fix target (for the pre-registered predictions, NOT done here): give ``evaluate_events``
a ``require_signature`` axis that mirrors the existing ``require_corroboration`` one —
spec key OR env ``OOPTDD_REQUIRE_SIGNATURE`` (default OFF), key via ``OOPTDD_SIGNING_KEY``;
run ``verify_chain`` over the gated events (canonicalizing with backend-stamped ``_*``
fields excluded) and set ``authenticated`` / ``unauthenticated`` (gating ``ok``) exactly
like ``uncorroborated``. ``sign_record`` is session-summary-shaped (its ``_SIGNED_FIELDS``
are ``total/passed/...``); the only general event-stream integrity primitive is
``sign_chain`` — which is why an event-path fix must use the chain.
"""
import pytest

from ooptdd.domain.model import correlation_keys, sign_chain
from ooptdd.engine.gate import evaluate_events

KEY = "harness-signing-key-9f3a"
CID = "auth-harness"

# The primary check is POSITIVE (present >= 1) — the forgeable-green direction. A forged
# event that satisfies it manufactures a clean pass; nothing on the gate path objects.
SPEC = {"cid": CID, "expect": [{"event": "deterministic", "op": ">=", "count": 1}]}
SPEC_REQ = {**SPEC, "require_signature": True}


def _rec(name: str) -> dict:
    """A bare domain event as an emitter would build it (pre-ship, pre-signature)."""
    return {**correlation_keys(CID), "service": "harness.svc", "level": "INFO", "event": name}


def _stamp(records: list[dict]) -> list[dict]:
    """Attach store-receive timestamps AFTER signing, mirroring a backend query. The `_*`
    fields are not part of the signed payload, so a correct verifier must strip them."""
    return [{**r, "_timestamp": i + 1} for i, r in enumerate(records)]


def _signed_stream(*names: str) -> list[dict]:
    """A legitimately hash-chained event stream, as an authenticated emitter would ship."""
    return _stamp(sign_chain([_rec(n) for n in names], KEY))


def _eval(spec, events, **kw):
    return evaluate_events(spec, events, reachable=True, complete=True, cid=CID, **kw)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    # Keep verdicts independent of ambient toggles so the harness measures ONLY authenticity.
    for var in ("OOPTDD_FORBID_ERRORS", "OOPTDD_REQUIRE_CORROBORATION",
                "OOPTDD_REQUIRE_SIGNATURE", "OOPTDD_SIGNING_KEY"):
        monkeypatch.delenv(var, raising=False)


# ── permanent guards: must stay GREEN now AND after the fix ─────────────────────────

def test_keyless_default_counts_an_unsigned_event():
    """The 0/6-adoption reality AND the back-compat lock: with no key configured, an
    UNSIGNED forged event still counts and the gate goes GREEN. This is the forgeable
    green in the exact mode every consumer runs today; a fix must remain opt-in and leave
    this untouched (breaking it would red every keyless consumer)."""
    legit = _signed_stream("started")                 # the real run emitted only `started`
    assert _eval(SPEC, legit)["ok"] is False           # `deterministic` absent -> honest RED
    forged = legit + _stamp([_rec("deterministic")])[:1]   # attacker appends one unsigned event
    forged[-1]["_timestamp"] = 99
    assert _eval(SPEC, forged)["ok"] is True            # forged green — undetected on the gate path


def test_require_signature_accepts_a_fully_signed_stream(monkeypatch):
    """No-false-alarm guard: a legitimately signed stream that satisfies the check must be
    GREEN even with enforcement ON. Stops a 'reject everything when a key is set'
    pseudo-fix. Green today (auth inert) and must stay green once the axis is wired."""
    monkeypatch.setenv("OOPTDD_SIGNING_KEY", KEY)
    monkeypatch.setenv("OOPTDD_REQUIRE_SIGNATURE", "1")
    res = _eval(SPEC_REQ, _signed_stream("started", "deterministic"))
    assert res["ok"] is True
    assert res.get("unauthenticated") in (None, False)   # never falsely flagged


# ── formerly RED-first (audit 비평-1), now wired: the gate path verifies the chain ──────

def test_require_signature_rejects_a_forged_event(monkeypatch):
    """The core of the gap: with enforcement ON, a stream whose gated evidence is an
    UNSIGNED injected event must NOT be a clean pass — the injection breaks the hash chain
    (no valid prev-link), so the verdict is unauthenticated/red, not green."""
    monkeypatch.setenv("OOPTDD_SIGNING_KEY", KEY)
    monkeypatch.setenv("OOPTDD_REQUIRE_SIGNATURE", "1")
    legit = _signed_stream("started")
    forged = legit + [{**_rec("deterministic"), "_timestamp": 99}]   # unsigned, off-chain
    res = _eval(SPEC_REQ, forged)
    assert res["ok"] is False
    assert res.get("unauthenticated") is True


def test_require_signature_rejects_a_tampered_payload(monkeypatch):
    """Revert-proof guard: proves detection depends on the actual signature, not a constant
    `authenticated=True`. We mutate a NON-event field of an otherwise-legit signed record
    AFTER signing — the count check still matches (event name untouched), so nothing but a
    genuine chain verification can red this."""
    monkeypatch.setenv("OOPTDD_SIGNING_KEY", KEY)
    monkeypatch.setenv("OOPTDD_REQUIRE_SIGNATURE", "1")
    stream = _signed_stream("started", "deterministic")
    stream[-1]["service"] = "spoofed.svc"      # tamper post-signature; stale sig_chain
    res = _eval(SPEC_REQ, stream)
    assert res["ok"] is False
    assert res.get("unauthenticated") is True
