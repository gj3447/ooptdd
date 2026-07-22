# Case studies — LTDD in production (anonymized)

Three real, current consumers of this exact library (vendored via
`scripts/vendor_ooptdd.py`). Details are anonymized; the shapes are not.
Adoption discipline applies throughout: **a receipt that only runs in its
author's session is not adoption** — every case below has its receipts resident
in CI, re-verified on every run, not demonstrated once and framed.

## 1. Industrial 3D-inspection line — 3,100+ tests shipping LTDD receipts

A machine-vision inspection system (PLC handshakes, 24-view 3D capture, Modbus
verdict registers) runs a pytest suite of ~3,100 tests. Every CI session ships
its outcome records to an external store and **positively verifies its own
arrival** before the session is allowed to conclude — the `session_finish`
build→ship→verify→policy loop, in `strict` mode.

Why it earns its keep there: the system's failure domain is *false OK* — a
verdict register that reads "pass" because a default value survived, not
because an inspection ran. The same inversion ooptdd applies to tests (never
trust the self-report) is the plant's acceptance rule for verdicts: a verdict
without a receipt is not a verdict. Gate specs pin event arrival per inspection
cycle, and `aggregate`/count checks bound cycle behavior.

## 2. Research-programme engine — "logs are ground truth" as an architecture

A research-tree engine (experiment registration, verdict lifecycle, rebuild
pipelines) uses LTDD as its observability backbone: its pytest conftest ships
every test outcome to the store keyed by a session cid, and its *rebuild*
subsystem is specified as an expected event trace
(`rebuild_start → env_check → step_exec×N → metric_compare → rebuild_verdict`)
— written RED-first, before the pipeline emitted anything. A crashed rebuild
step must surface as `step_failed`, asserted by gate, so the honesty property
("a crash is never relabeled as a metric mismatch") is pinned by trace, not by
code review.

## 3. Cross-language verification — a Rust substrate judged by this verifier

A Rust P2P substrate implements the LTDD envelope natively and emits
ooptdd-compatible JSON (`cid` + `event` + flat attrs). Its receipts are judged
by *this* Python verifier in a separate process — the strongest
generator≠verifier separation in the family: the judge does not share a
language, a runtime, or an author-session with the system under test. Same
three-valued verdict; `Inconclusive` is likewise forbidden from hard-failing.

## The pattern across all three

- The store is the judge; the self-report is a claim.
- RED-first event specs, in-repo as data (YAML / test constants).
- INCONCLUSIVE is a first-class outcome — infra blindness never masquerades as
  falsification.
- Receipts live in CI. If it doesn't re-verify on every run, it doesn't count.
