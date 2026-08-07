# Ouroboros verification matrix

The matrix distinguishes an implemented fact from a future operational claim.

| Claim | Executable evidence | Main counterexample | Status |
|---|---|---|---|
| Transition selection is deterministic | reducer value tests | same input produces a different phase/effect ID | implemented |
| Exact cycle-scoped replay is idempotent | replay test | duplicate advances accepted history or changes effect ID | implemented |
| Event IDs are not last-writer-wins keys within a cycle | collision and cycle-mismatch tests | same cycle/ID accepts another intent | implemented |
| Phase evidence is identity-separated and source-bound | run guard and receipt validation tests | two phases share run ID/namespace, or a run names the wrong baseline/mutated source | implemented |
| Run vocabularies stay distinct while positive phases require present readback | enum and phase tests | GREEN plus absent readback confirms, or scalar monitor diagnostics control the lifecycle | implemented |
| Mutation cannot leak through termination | interrupt and budget restoration tests | terminal snapshot retains active mutation | implemented |
| Bite is total | finding/disposition and successor tests | an undisposed finding seals | implemented |
| Bound changes supersede the generation; the safe constructor binds a validated immediate predecessor | supersession, receipt-lineage, relabelling, and successor-construction tests | changed material completes in generation `g`, trace is relabelled, or constructor uses post-validation mutable data | implemented locally |
| Receipt self-identity is strict change detection | canonical/raw/tamper tests | field edit retains the same valid self-hash | implemented; not authenticity |
| Receipt trace can drive semantic replay | accepted-event envelope and phase-edge tests | trace omits canonical payload or cannot reconstruct an event | implemented |
| Abstract FSM is internally covered | FSM validator and trace runner | transition/guard/invalid policy lacks a trace | implemented |
| Bounded-final gate output becomes typed receipt evidence | gate-adapter projection, reducer-compatibility, numeric-domain, and mutation tests | optional/pending/custom context is lost, a negative/zero-total weight forges quorum, or scalar monitor controls lifecycle | implemented by the versioned gate-evidence artifact; receipt v2 wire shape unchanged |
| Evidence tier is derived rather than caller-selected on the gate-adapter path | tier recomputation, non-gating causal-check, sampled-cap, and external-identity tests | an optional/pending check promotes weak evidence, or a caller promotes arrived evidence to causal/external without the required gating check | implemented structurally on the adapter path |
| Crash recovery and effect delivery | none in this module | process dies between state and external effect | deferred |
| Evidence producer and oracle authority are authenticated | no detached attestation or externally owned anchor in this module | a colluding producer fabricates a self-consistent verifier result and boundary | authenticated store adapter and external anchor required |
| Authenticated gate completion resolves the roles and artifact types named by an explicit frozen policy | `resolve_and_assess_authenticated_gate_completion` tests with 0/1/3/4-role policies and injected resolver and authority ports | a locally replay-valid receipt names a missing, duplicate, non-canonical, context-mismatched, digest-mismatched, unauthenticated, dependent, wrong-authority, or wrong-contract artifact | implemented as an opt-in fail-closed policy; the explicit v2 preset reproduces the four-role behavior, exact bytes and full receipt context are validated before authority invocation, and deployment trust-root selection remains caller-owned |
| Independent-store arrival in production | existing backend integrations, not this protocol slice | store/SUT share authority | adapter evidence required |
| Caller loop terminates | none in this module; invalid/replayed calls do not consume `max_steps` | caller invokes forever without admissible progress | caller-owned retry, timeout, no-progress, and invocation bounds required |
| Reducer/receipt producer is authentic | none in this module | attacker rewrites receipt and recomputes its unkeyed hash | out of scope |
| A claimed predecessor hash exists in an authoritative chain | none in this module | self-consistent successor names an unavailable predecessor | chain validator or authenticated store required |
| Scientific or Lakatosian progress | no judge in this module | internally consistent but degenerating cycle | out of scope |

Authenticated gate completion is deliberately narrower than an authoritative cycle claim.
It authenticates the exact canonical gate-artifact bytes selected by a locally valid receipt;
it does not authenticate the receipt producer, prove lineage availability, establish oracle
truth, or make a caller-provided authority independent in fact. Those remain separate trust
and deployment obligations.

The authority port is not a cryptographic identity mechanism. Its ``authority_id`` is a claim
returned by the caller-selected verifier and compared with policy; callers must bind the concrete
verifier to an authenticated trust root through deployment configuration or a stronger adapter.

## Verification commands

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q -n 2
.venv/bin/ruff check src tests examples
.venv/bin/mypy src/ooptdd/ouroboros
```

The FSM artifacts additionally validate with the `fsm-design` reference validators, and
the module decision validates with the `engine-design` reference validator.  CI-portable
contract tests independently pin their essential structure so repository verification
does not depend on a personal plugin installation.
