# Ouroboros generic workflow kernel

`ooptdd.ouroboros` is a small functional kernel for bounded, deterministic workflows.
It is not a testing methodology, a mutation runner, a Git wrapper, or a background
orchestrator. Applications import the kernel and supply an immutable protocol definition
for their own state and event vocabulary.

## Boundary

The base package exports only:

- opaque string state, event, workflow, and material-revision identities;
- immutable protocol, budget, snapshot, event, and transition values;
- versioned payload validation, completion, and recovery policy declarations;
- the pure `step(snapshot, event, evaluator)` function;
- caller-owned policy-evaluation, snapshot-store, and receipt-store ports.

There is no global protocol registry or default profile. Importing
`ooptdd.ouroboros` has no profile-registration side effect. A policy digest covers the
declared states, transitions, evaluator and validator versions, completion requirements,
and recovery rules. Snapshots and events carry that digest, so replay under a different
policy fails closed. Policy definitions contain no Python callables: an explicitly
injected `PolicyEvaluator` supplies behavior and must identify the exact policy digest
and evaluator version it implements.

## Minimal example

```python
from ooptdd.ouroboros import (
    CompletionPolicy,
    CompletionEvidence,
    ProtocolBudget,
    ProtocolDefinition,
    ProtocolEvent,
    ProtocolSnapshot,
    RecoveryPolicy,
    RevisionIdentity,
    TransitionRule,
    step,
)

protocol = ProtocolDefinition(
    name="door",
    version="door/v1",
    evaluator_version="door-evaluator/v1",
    states=("closed", "open"),
    events=("unlock",),
    initial_state="closed",
    transitions=(TransitionRule("closed", "unlock", "open"),),
    completion=CompletionPolicy("completion/v1", ("open",)),
    recovery=RecoveryPolicy("recovery/v1"),
)
snapshot = ProtocolSnapshot.initial(
    "door-7",
    RevisionIdentity("release", "release-2026.08"),
    protocol,
    ProtocolBudget(max_steps=2, max_generations=1),
)
event = ProtocolEvent.create("event-1", "unlock", {}, protocol)
class DoorEvaluator:
    definition = protocol
    version = "door-evaluator/v1"
    policy_digest = protocol.digest
    def validate_payload(self, name, version, payload):
        return True
    def evaluate_completion(self, version, payload):
        return CompletionEvidence(True)

result = step(snapshot, event, DoorEvaluator())
assert result.accepted and result.snapshot.state == "open"
```

Unknown events, missing transitions, invalid payloads, exhausted budgets, terminal-state
advances, and policy mismatches return an unchanged snapshot with an explicit rejection
code. A repeated event ID replays only when its full intent and policy digests match.

## Completion and recovery

Completion is caller-defined. `CompletionPolicy` names its terminal states and required
authority and artifact identities. The evaluator returns typed `CompletionEvidence`;
the kernel independently checks that every declared requirement is present, so an
evaluator cannot satisfy completion merely by returning a boolean. The kernel contains
no hidden role count or artifact vocabulary. `RecoveryPolicy` similarly declares the
state/event pairs allowed to leave recovery states. Evaluator behavior must receive a new
version whenever it changes.

An initial state may also be terminal only for unconditional completion. If a protocol
requires completion authorities or artifacts, it must reach the terminal state through
a transition where the evidence can be evaluated. Definitions that combine an initial
terminal state with those requirements are rejected fail-closed.

## Receipts and effects

`receipt_from_snapshot` emits a canonical, self-digested generic receipt only from a
terminal, policy-matched snapshot. `validate_receipt` recomputes its content digest and
checks its policy, terminal state, history structure, initial state, and every declared
transition. This is deterministic integrity validation, not authentication or an
independent replay of external completion evidence; callers that need those properties
must add an authenticated envelope and retain evaluator evidence. `ReceiptStore`
persists the canonical bytes by digest; storage does not become part of the pure
transition.

The generic kernel deliberately has **no effect intent vocabulary**. `step` returns only
an immutable decision. A caller may persist its accepted snapshot with `SnapshotStore`
and its terminal receipt with `ReceiptStore`, but retries, I/O, messages, and domain
effects remain outside the kernel. This avoids silently prescribing effect semantics to
unrelated workflows.

## Opt-in OOPTDD mutation profile

The historical red/green/mutation evidence cycle is shipped in the separately installable
first-party `ooptdd-mutation` distribution, not the base wheel:

```python
from ooptdd_mutation.ouroboros import CycleSnapshot, EventKind, step
```

Its gate artifacts, receipt schemas, completion authorities, and repository-object
validation remain profile-specific. See
[`ouroboros/ooptdd_mutation/`](ouroboros/ooptdd_mutation/) for that
workflow and its case study. Importing or installing the generic kernel does not load or ship it.
