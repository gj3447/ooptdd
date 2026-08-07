# OOPTDD/Ouroboros readiness harness

The current result is deliberately asymmetric:

- the generic, caller-driven Ouroboros **core profile is ready** for its documented local
  purpose;
- the maintainability, durable-runtime, and authenticated-authority profiles are **not ready**;
- no missing runtime guarantee is promoted merely because a port or design document exists.

This is an `L_IDE` source and evidence harness evaluating a future `L_RT` boundary. It does not
run workflows, persist state, deliver effects, or administer a trust root. The machine authority is
[`ooptdd-ouroboros-readiness-contract.json`](ooptdd-ouroboros-readiness-contract.json).

> **Repository checkout only.** Run the commands below from a complete Git checkout. The sdist
> retains this contract and handbook for inspection, but intentionally omits the checker and its
> repository test evidence; neither the wheel nor sdist is an independently executable readiness
> bundle.

## IVCC control loop

| Axis | Mechanism |
|---|---|
| Inform | This catalog names every current capability, gap, falsifier, solution, and acceptance condition. |
| Constrain | The checker rejects unknown fields, unsupported status vocabulary, dependency cycles, orphan capabilities, false profile status, and `PASS` without executable evidence. |
| Verify | Every passing capability points to a live Python or test symbol; canary tests prove that planted contract drift is detected. |
| Correct | Every non-passing capability appears once in a dependency-ordered remediation sequence with an explicit exit condition. |

Run the currently enforced profile:

```console
python scripts/check_ooptdd_ouroboros_readiness.py
```

Inspect a promotion target; exit status `1` is intentional while its blockers remain:

```console
python scripts/check_ooptdd_ouroboros_readiness.py --profile maintainability
python scripts/check_ooptdd_ouroboros_readiness.py --profile runtime
python scripts/check_ooptdd_ouroboros_readiness.py --profile authority
python scripts/check_ooptdd_ouroboros_readiness.py --profile runtime --format json
```

The command exits `0` only for a ready profile with intact evidence, `1` for a truthful
not-ready profile or evidence drift, and `2` for an invalid contract.

## What is closed now

| Capability | Current guarantee |
|---|---|
| `KERNEL-REDUCER-001` | Immutable values and the injected `step` reducer retain deterministic replay, identity-conflict, policy-binding, and unchanged-snapshot rejection behavior. |
| `KERNEL-RECEIPT-002` | The v1 parser accepts only `ouroboros-receipt/v1`; a self-consistent unknown version no longer slips through the v1 interpretation path. |
| `KERNEL-FSM-003` | `analyze_definition` reports reducer-executable reachability, terminal outgoing rules, incomplete recovery pairs, denied recovery edges, and the fixed invalid-event policy. It intentionally does not guess validator satisfiability or liveness. |
| `KERNEL-SUCCESSOR-004` | `start_successor` now requires the predecessor snapshot to pass structural terminal-receipt construction. It remains a local integrity helper, not authenticated or durable lineage. |
| `KERNEL-ARCH-005` | The existing functional/SOLID harness classifies the new pure analyzer and keeps domain vocabulary and ambient effects out of the generic base. |
| `KERNEL-CLAIMS-006` | Readiness is derived from required capability statuses; changing a label cannot make an unmet profile ready. |

These statements are narrower than “Ouroboros is a production runner.” The reducer still delegates
evaluator behavior, storage, retries, scheduling, and effects to callers.

## Maintainability gaps

### `FRAMEWORK-EXTENSIONS-010` — version-negotiated CLI extension activation

The Python composition API can receive explicit providers, but the shipped CLI cannot yet lazily
resolve an explicitly requested installed provider with a negotiated extension API version. Solve
this with a frozen descriptor `(name, api_version, provider)`, an `ooptdd.extensions` entry-point
catalog, and resolution of requested names only. Legacy directly injected callables can remain a v1
compatibility path. Tests must prove that unrequested providers never load, version mismatch fails
before invocation, load order is deterministic, and partial activation is impossible.

### `FRAMEWORK-KERNEL-CONVERGENCE-011` — generic/mutation conformance

The mutation extension has a stronger lineage/effect model but separately implements similarly
named event, budget, result, and reducer types. Replacing or aliasing those types now would be a
breaking semantic refactor. First define a small reducer-conformance protocol and run both kernels
through the same domain-neutral replay, conflict, terminal, and exhaustion invariants. Domain
effects stay in the mutation extension; convergence must not move mutation terminology into base.

## Runtime gaps and their solutions

### Identity and deterministic replay

| Capability | Missing fact | Solution boundary | Promotion evidence |
|---|---|---|---|
| `RUNTIME-LINEAGE-020` | Generic v1 events do not bind workflow, generation, material revision, expected revision, or predecessor. | Add versioned event/successor envelopes; do not change existing v1 intent hashes. | Cross-workflow, cross-generation, stale-revision, and predecessor-swap negatives. |
| `RUNTIME-EVALUATOR-025` | An arbitrary injected evaluator can consult ambient state, and accepted history does not retain its decision inputs/outputs. | Persist canonical decision records with evaluator digest, typed output, and external-fact references. | Offline replay without clock, randomness, network, or mutable configuration. |
| `RUNTIME-REPLAY-EVIDENCE-030` | Receipt v1 retains transition intents but not canonical payloads or completion decisions. | Define receipt v2 containing event envelopes, decision records, completion evidence, and lineage; keep v1 integrity-only. | Reconstruct every accepted event and independently recheck the terminal decision. |

### Bounded loop semantics

| Capability | Missing fact | Solution boundary | Promotion evidence |
|---|---|---|---|
| `RUNTIME-BOUNDS-040` | Only accepted steps and generations are bounded; reject/replay calls can continue forever. | Introduce immutable total-invocation, retry, correction, no-progress, deadline, cost, tool-call, and cancellation limits at the runner boundary. | Exhaust every bound with a typed, bounded stop and no extra effect. |
| `RUNTIME-OUTCOMES-050` | Exhaustion and cancellation are not durable terminal outcomes. | Model completed, rejected, cancelled, exhausted, failed, and quarantined outcomes with reason, retryability, checkpoint, and pending-effect state. | Every run is runnable or has exactly one typed terminal outcome. |

The loop control belongs in a runtime shell. Expanding `ProtocolBudget` alone would mix process
policy into a reusable value reducer and still would not make I/O durable.

### Durable narrow waist

The required execution boundary is:

```text
versioned command
    -> pure decide(snapshot, command, recorded facts)
    -> events + snapshot + effect intents + typed outcome
    -> one durable inbox/journal/state/outbox transaction
    -> fenced effect workers
    -> checkpoint, reconciliation, and receipt publication
```

| Capability | Required implementation and proof |
|---|---|
| `RUNTIME-JOURNAL-060` | A real append-only adapter with canonical checksummed records, durable acknowledgement, unique workflow sequence, torn-tail detection, and replay equivalence. An in-memory fake cannot establish this. |
| `RUNTIME-CAS-070` | Atomic compare-and-swap over workflow, generation, revision, schema/policy version, and fencing epoch. Multiprocess tests must yield one winner and rule out generation-reset ABA. |
| `RUNTIME-INBOX-OUTBOX-080` | Command deduplication, event, snapshot, outcome, and effect intents commit together. Crash injection at each transaction boundary must observe all or none. |
| `RUNTIME-IDEMPOTENCY-090` | Effect identity binds canonical intent, causation, and ordinal. Adapters are idempotent or explicitly guarded; attempts and ambiguous acknowledgements remain reconcilable. |
| `RUNTIME-CHECKPOINT-100` | Checkpoints bind workflow, generation, revision, journal position, checksum, policy, and schema. Checkpoint plus suffix replay must equal full replay; corrupt newest checkpoints fall back safely. |
| `RUNTIME-LEASE-110` | Durable single-writer leases attach monotonically increasing fencing epochs to every state and outbox write. A stale owner must fail after takeover. |
| `RUNTIME-MIGRATION-120` | Every event, decision, snapshot, checkpoint, and receipt version is current, deterministically upcastable, read-only, or rejected. Golden old bytes and crash-safe migration tests are mandatory. |
| `RUNTIME-OPERATIONS-150` | Bounded admission/queues, idempotent durable cancellation, workflow queries, and metrics/traces for age, no-progress, retries, lease epoch, and pending effects. |
| `RUNTIME-RECONCILIATION-130` | An idempotent reconciler repairs or quarantines journal/checkpoint drift, abandoned ownership, pending effects, schema state, and terminal receipt publication. |
| `RUNTIME-FAULT-MATRIX-140` | This remains `BLOCKED` until the real adapter exists. Then process death is injected around every durable write, external call, acknowledgement, renewal, checkpoint, migration, and publication boundary. |

The first durable implementation should be one transactional adapter, not several nominal ports
with no shared atomicity. Journal, inbox, state, and outbox coherence is the engine's narrow waist;
additional storage technologies can implement the same conformance suite later.

## Trust boundary

### `AUTH-ENVELOPE-200` — authenticated producer and receipt

The v1 content digest detects accidental or untrusted-byte modification only when the validator has
an independently trusted original. It does not identify the producer: an attacker can edit the
body and recompute an unkeyed hash. An optional detached attestation should cover the exact
canonical receipt bytes and bind authority ID, algorithm, key version, and external context. The
generic package supplies only a verifier port and typed result; deployment owns trust-root choice,
revocation, and secrets. Integrity, producer authenticity, evidence independence, and predecessor
availability must remain separate reported facts.

## Remediation order

1. Freeze extension negotiation and the cross-kernel conformance suite.
2. Freeze v2 lineage, decision, loop-limit, outcome, and optional attestation contracts.
3. Make receipt v2 semantically replayable while retaining explicit v1 compatibility.
4. Implement one real journal, then generation-aware CAS.
5. Add the atomic inbox/state/outbox transaction and bounded operational surface.
6. Add effect idempotency, checkpoints, and fenced ownership.
7. Add schema migration and idempotent reconciliation.
8. Run the complete crash/concurrency fault matrix; only then change the runtime profile to ready.

This order keeps the generic reducer small and functional while moving operational policy into a
replaceable runtime shell. No domain workflow, testing methodology, project identity, storage
vendor, or deployment secret becomes a base-package default.
