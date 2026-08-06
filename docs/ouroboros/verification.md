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
| v1 migration does not invent proof | upcast tests | v1 becomes complete without new evidence | implemented |
| Abstract FSM is internally covered | FSM validator and trace runner | transition/guard/invalid policy lacks a trace | implemented |
| Crash recovery and effect delivery | none in this module | process dies between state and external effect | deferred |
| Evidence-tier and oracle claims are authenticated | none in this module | caller labels its own output `external_verdict` | adapter evidence required |
| Independent-store arrival in production | existing backend integrations, not this protocol slice | store/SUT share authority | adapter evidence required |
| Caller loop terminates | none in this module; invalid/replayed calls do not consume `max_steps` | caller invokes forever without admissible progress | caller-owned retry, timeout, no-progress, and invocation bounds required |
| Reducer/receipt producer is authentic | none in this module | attacker rewrites receipt and recomputes its unkeyed hash | out of scope |
| A claimed predecessor hash exists in an authoritative chain | none in this module | self-consistent successor names an unavailable predecessor | chain validator or authenticated store required |
| Per-check monitor aggregation is lifecycle-authoritative | scalar diagnostic only | optional/gating/threshold context is lost | typed adapter evidence required |
| Scientific or Lakatosian progress | no judge in this module | internally consistent but degenerating cycle | out of scope |

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
