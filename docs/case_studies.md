# Case studies — LTDD in real consumers (anonymized, wiring-accurate)

Three real, current consumers of this exact library. Details are anonymized;
the shapes are not — and neither are the caveats. An adoption claim here states
**where the receipt actually runs** (blocking CI job / local opt-in gate /
manual harness), because a receipt that only runs in its author's session is
not adoption, and a doc that rounds "opt-in local gate" up to "every CI run"
would be committing exactly the sin this library exists to catch.

## 1. Industrial 3D-inspection line — a ~3,100-test suite with LTDD wired in

A machine-vision inspection system (PLC handshakes, 24-view 3D capture, Modbus
verdict registers) runs a pytest suite of roughly 3,100 tests with this
library's session hook vendored in: when armed, every session ships its outcome
records and **positively verifies its own arrival** before concluding — the
`session_finish` build→ship→verify→policy loop.

Wiring honesty: shipping is an explicit opt-in (an env flag; offline runs stay
silent by design), verification defaults to `warn` with `strict` available via
`OOPTDD_VERIFY`, and the backend is the team OpenObserve only when its URL is
configured (else the in-process store, which proves mechanics, not arrival).
Enforcement lives in local/pre-push gates — the project's hosted CI does not
run this suite. What the case demonstrates is the *integration shape* at scale,
not CI-resident enforcement.

Why LTDD earns its keep there: the system's failure domain is *false OK* — a
verdict register that reads "pass" because a default value survived, not
because an inspection ran. The same inversion this library applies to tests
(never trust the self-report) is the plant's acceptance rule for verdicts: a
verdict without a receipt is not a verdict.

## 2. Research-programme engine — receipts as a blocking CI job

A research-tree engine (experiment registration, verdict lifecycle, rebuild
pipelines) is the strongest CI story of the three: its hosted CI re-runs a
panel of 13 design-audit **ooptdd receipts on every push, as a blocking job**.
Its pytest conftest can additionally ship every test outcome to the external
store keyed by a session cid (env-gated opt-in, a documented no-op offline —
including in that CI job, which runs hermetically).

Its *rebuild* subsystem is specified as an expected event trace
(`rebuild_start → env_check → step_exec×N → metric_compare → rebuild_verdict`),
self-labelled RED-first in the spec. A crashed rebuild step must surface as
`step_failed`, pinned by a test over the emitted trace (an in-process emit
assertion, not a store-readback gate) — so the honesty property "a crash is
never relabeled as a metric mismatch" is trace-pinned.

## 3. Cross-language verification — a Rust substrate judged by this verifier

A Rust P2P substrate implements the LTDD envelope natively and emits
ooptdd-compatible JSON. Its verification harness runs 8 Rust emitters (in
Docker) and judges each trace with **this Python verifier in a separate
process** — including five adversarial RED wings asserting the gates actually
fire. That is the strongest generator≠verifier separation in the family: the
judge shares no language, runtime, or author-session with the system under
test.

Wiring honesty: the harness is a manual script (Docker + local paths), not CI —
the crate's own `cargo test` pins only the envelope shape. Cross-language
judging is demonstrated and re-runnable, not continuously enforced.

## The pattern across all three

- The store is the judge; the self-report is a claim.
- RED-first event specs, in-repo as data (YAML / test constants).
- INCONCLUSIVE is a first-class outcome — infra blindness never masquerades as
  falsification.
- And the discipline this page holds itself to: say where the receipt runs.
  Blocking-CI (case 2) > local opt-in gate (case 1) > manual harness (case 3),
  and only case 2 currently clears the "resident in CI" bar.
