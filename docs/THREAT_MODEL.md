# Threat model — what a GREEN gate does and does not prove

An adversarial grill (2026-07-22) forged `gate["ok"] == True` four different
ways against a malicious system-under-test (SUT). None of those forgeries is a
bug — they mark the **boundary of the guarantee**, and a library whose whole
thesis is "don't trust an unverified claim" must state that boundary plainly
rather than let the marketing imply more. This document is that statement.

## The one guarantee that is robust: silent-loss / arrival detection

ooptdd's novel, load-bearing guarantee is this: **an event the system was
supposed to emit, but didn't (or that was silently dropped in transit), is
caught** — the verifier reads the store back and returns `absent` (⊥), even
though the SUT's own return value / logs said "OK". This is the founding
incident (`examples/openobserve_demo/demo_silent_401.py`) and it holds: the
grill's honest-failure controls (dropped ingest, empty gate, all-optional gate,
unsigned event, no verifier key) all correctly produced `ok == False`.

If you take one thing from ooptdd, take this: it converts "the function
returned OK" into "the evidence actually landed in an independent store." That
is a real, testable upgrade over a return-value assertion.

## What a GREEN gate does NOT prove: the SUT did honest work

The store's contents are **written by the SUT** — the SUT is the emitter. So a
verifier reading the store sees only what the defendant chose to write. Against
a SUT that actively controls its own process, env, and the gate it proposes,
the anti-forgery layers reduce to **self-consistency, not correctness**:

| Forgery | Why it works | What the layer actually defends |
|---|---|---|
| Ship the named events directly, do no work | the SUT is the emitter; `ok` reads arrival + shape only | nothing — this is the base case |
| `require_signature` + forged chain | the signing key lives in the SUT's own env; it signs the forgery | an **out-of-band store editor**, not the SUT |
| `require_corroboration` + colluding probe | `external:` `separate_source` is a **self-declaration** the SUT's probe asserts | a probe the SUT genuinely cannot author |
| Propose a weakened gate (`threshold: 0.2` + `justification:`) | the SUT proposes the gate; lint's VAC2 is silenced by free text | a **baseline strength fingerprint pinned by the gatekeeper**, out-of-band |

The honesty fields already expose every one of these — `oracle.single_authority`,
`oracle.corroborated`, `oracle.emit_backend`, `scope.charge_ratio`,
`evidence_tier`, `grounding` — but historically they **informed** rather than
**gated**: a consumer who asserts only `gate["ok"]` (as older README examples
taught) inherited none of that protection.

## What changed after the grill

- `caps.independent` is no longer dead data. A spec may set
  `require_independent_store: true` (or export `OOPTDD_REQUIRE_INDEPENDENT=1`);
  under it, `ok` becomes False when the emitting backend is non-independent
  (in-process `memory`, same-host `jsonl`) **and** no gating check is
  corroborated by a separate source — turning "this green rode on a
  same-process judge" from an advisory sidecar into a verdict.
- The `require_signature` / `require_corroboration` threat scope is documented
  here and cross-referenced from their docstrings: they defend against an
  out-of-band tamperer and honest mistakes, **not** a SUT that holds the key or
  supplies the probe.

## How to actually gate an untrusted agent

If the SUT is untrusted (e.g. you are gating an AI agent's work), the trust
anchor must live **outside** the SUT's process:

1. **The store must be independent** — a team OpenObserve/ClickHouse the agent
   cannot write arbitrary rows to under the verifier's identity. Set
   `require_independent_store: true`. A `memory`/`jsonl` green proves gate
   mechanics only (see `docs/backends.md`).
2. **The gate is pinned by the gatekeeper, not proposed by the SUT** — commit
   the gate spec and a `strength` baseline out-of-band; run
   `ooptdd strength --baseline` in CI so a weakened gate is a REGRESSION.
3. **Corroboration must be genuinely external** — the `external:` probe reads a
   source the SUT does not author (a real DB row, a downstream service's own
   telemetry), run by the verifier, not handed in by the SUT.
4. **Signatures authenticate a writer you control** — sign at an ingest gateway
   the SUT cannot impersonate, not inside the SUT.

Without those, a GREEN gate means "the SUT's self-reported events are internally
consistent with the SUT's proposed spec" — useful for catching honest silent
loss, but not a proof of honest work.
