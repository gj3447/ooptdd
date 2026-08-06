# Ouroboros — auditing the test system itself, and where the regress ends

[`ooptdd mutate`](ci_mutation_gate.md) answers "would my gate notice a broken
*product*?" This page answers the next question up: **would anything notice a
broken *test system*?** — and shows that the "who watches the watchers" regress
terminates at a finite height with a *computable* residual, rather than
disappearing.

The pattern is named **Ouroboros** because the top layer closes the regress by
biting its own tail: it mutates its own source and checks that its own controls
notice.

## The incident that motivated it

In a host repository (2026-08-03), two pinned behavioral tests had been red for
three days without anyone knowing. Two independent holes stacked:

1. the test file was not in any CI/gate list — it existed, and never ran;
2. when run manually, a site-packages `.pth` fallback resolved the package
   import to a **stale clone 114 commits behind**, so the run was green against
   the wrong code.

Neither hole is a product bug, and no product-facing gate can see either. They
are faults *of the test system*, and they compose: an unrun test is silent, a
mis-imported test is worse than silent.

## The four layers

| layer | watches | mechanism |
|---|---|---|
| L1 | the product | ordinary behavioral tests / trace gates |
| L2 | the *infrastructure* of L1 | **gate-coverage ratchet** (every test file is in a gate list or in a frozen, reasoned baseline — new leaks fail) + **import identity** (the module under test must resolve inside this repo) |
| L3 | the L2 checker | **positive controls**: synthetic violations fed to the audit functions; each must be flagged, and a clean input must not be |
| L4 | the L3 controls | **self-referential mutation + sentinel pair**: read own source, inject lethal mutants into the audit functions, run the shared controls against the mutant in an isolated namespace. Every lethal mutant must be killed; a benign mutant (comment-only) must survive |

The sentinel *pair* is the load-bearing part. A checker has exactly two
degenerate states — constant-PASS (vacuous) and constant-FAIL (trigger-happy) —
and one witness excludes each: a planted fault that must be flagged rules out
constant-PASS; a planted non-fault that must not be flagged rules out
constant-FAIL. Two witnesses are therefore necessary and sufficient for
non-degeneracy, which is why a fifth layer has nothing left to certify: it
could only ever supply another finite probe pair.

## Where the regress ends — the termination result

Model gates as classifiers `g : states → {PASS, FAIL}`, fix a fault model
𝔉 (a set of edits), and let `Det(G)` be the faults a gate stack observably
flips. Meta-layering is the monotone operator
`Φ(C) = C ∪ {faults of the checkers of C}` on the finite lattice `(2^𝔉, ⊆)`,
so by Knaster–Tarski the ascending chain `Det(G₁) ⊆ Det(G₂) ⊆ …` stabilizes.
Kleene's second recursion theorem is what makes the self-referential L4
legitimate (the same reason quines exist).

The honest form of the termination theorem — found by *implementing* the model
and having it refute the first draft — is:

> Under single-edit faults, the residual `𝔉 − Det(G_n)` shrinks monotonically
> and reaches, at height 4, the **top reporting statement of the top layer**
> (silence that one line and no observation changes). Adding a fifth layer
> catches it — and introduces its own reporting statement. **The residual's
> size is invariant (≥ 1) from height 4 up.** Meta-layers do not eliminate the
> trusted computing base; they *shrink and relocate* it. Height 4 is the least
> height at which it is a single statement.

What retires the residual is not another layer but **external anchors**:
commit hashes, signed receipts, review — exactly the things a repository keeps
outside its own test process.

## The anchor layer, measured

That sentence is where the regress argument stops, so it is worth asking what
the anchors are actually worth once you have them. Probing the evidence side
the way L4 probes the gate side (2026-08-05) turned up two things.

**An anchor nobody is required to consult retires nothing.** Anchoring tends to
arrive as an available command: a `--anchor` flag, a separate `anchor-check`
CLI, a reviewer who *may* look. Each is individually defensible and together
they mean a green can be produced having touched nothing outside the process
that authored it. The residual has not moved; it has been given a door it can
walk through. A gate that means it treats *unverifiable* — "I could not reach
the anchor" — as a failure, because "I could not check" and "it checked out"
must not share an exit code.

**A chain cannot testify to its own length.** `verify_chain` catches edits,
interior deletions and reorders. It does not catch truncation, in either
`evolve` mode: drop the trailing records and every surviving link is genuinely
valid, because the length was never in the records. Wholesale re-signing by a
key holder is the same class. Both are invisible to any amount of self-
verification and visible to a one-line external expectation, which is why
`verify_chain` now takes `expect_len` / `expect_head` — and why they only help
if the value comes from somewhere the writer does not control.

| attack | bare `verify_chain` | with an external expectation |
|---|---|---|
| edit / interior delete / reorder / strip record `sig` | detected | detected |
| tail truncation (any depth) | **invisible** | detected |
| wholesale re-sign by the key holder | **invisible** | detected (`expect_head`) |

`tests/test_chain_truncation.py` pins both halves, and the boundary half is
written to fail if a bare call ever starts detecting truncation — at which
point this section is what should be revised, not the test. Asserting a hole
as a hole is the same discipline as the sentinel pair above: the temptation to
write the test one wishes were true is exactly the vacuity being priced.

This is the same shape as the L4 termination result, one level out. Layers
relocate the trusted base; anchors retire it — but only the anchors that are
*mandatory*, *externally sourced*, and *counted when absent*. The prior art
worth reading here is transparency-log witness cosigning (a witness verifies a
consistency proof before co-signing, and a quorum of independent witnesses is
what defeats a split view) and Wheeler's diverse double-compiling, which is
Thompson's exit in the bullet above made constructive: the second checker has
to be a *different implementation*, not the same one run twice.

## Boundaries (what this does not prove)

- **No absolute termination** (Gödel-II analogue): "the stack is sound" in
  full generality is not provable inside the stack; termination is always
  relative to a declared fault model.
- **Finite probes are gameable** (Rice): a "Volkswagen mutant" that behaves
  correctly exactly on the sentinel inputs escapes any finite probe set.
- **Coordinated rewrites escape any finite tower** (Thompson, *Reflections on
  Trusting Trust*): edit every layer and its sentinels consistently and the
  observations are preserved. Diverse toolchains and out-of-band hashes are
  the only exits.
- **Discrete-world note**: because source states are finite bit strings, the
  residual is *computable by enumeration* once the fault model is fixed — the
  impossibility results reappear as (a) combinatorial explosion in edit
  distance and (b) self-inclusion of the runner (pytest, the interpreter, the
  OS are bits of the same state space; putting them in the fault model moves
  the residual into their reporting paths, it does not remove it).

## Measured instance

Host repo, 2026-08-03. The four layers are one pytest file wired into the
standard gate. Exhaustive single-line silencing of its 128 functional lines
(each line replaced by `pass`, gate re-run per mutant, **null control first** —
the unmutated copy must pass in the harness before any mutant is scored):

```
killed 123 / survived 5
  structural residual 2   (the L4 result-collection line — the predicted
                           top-statement class — and the L3 top assert)
  dormant guard 1         (protects a condition that is currently false)
  benign 2                (__future__ import; an idempotent sys.path insert)
```

Two footnotes worth keeping: the *first* census returned 128/128 killed — an
instrument failure (the harness ran mutants where a config path didn't exist,
so even the unmutated copy died), caught only because the null control was
added; and multi-line `assert` statements enjoy accidental syntactic
protection (silencing their first line is a SyntaxError, hence detected) — the
true single-point silence was the result-collection line *before* the assert.

## Relation to `ooptdd mutate`

Same epistemology, different direction. `ooptdd mutate` plants failures in the
*subject* to price a gate's credibility; Ouroboros plants failures in the
*gate system* (including itself) to price the credibility of the whole testing
apparatus — and states exactly what remains on trust when it is done: one
reporting statement, the runner, and the anchors outside the repo.

## Reference implementation sketch

```python
# L2 — coverage ratchet + import identity
def audit(gated, discovered, baseline):
    return {
        "new_ungated":   sorted((discovered - gated) - baseline),
        "stale_baseline": sorted((baseline & gated) | (baseline - discovered)),
        "missing_gated": sorted(gated - discovered),
    }
assert module_under_test.__file__ starts inside THIS repo

# L3 — shared positive controls (used by both L3 and L4)
def run_controls(audit_fn, parse_fn) -> list[str]:  # failed control names
    ...

# L4 — self-referential mutation + sentinel pair
for lethal in LETHAL_MUTANTS:                # e.g. "uncovered = set()"
    mutant = exec_in_isolated_ns(own_source.replace(lethal.old, lethal.new))
    assert run_controls(mutant.audit, mutant.parse) != []   # must be killed
benign = exec_in_isolated_ns(own_source.replace(COMMENT, COMMENT + " (benign)"))
assert run_controls(benign.audit, benign.parse) == []       # must survive
```

A full executable model of the termination result (monotonicity, residual
invariance, minimal height, Volkswagen and collusion witnesses as machine-
checked negative controls) lives in the host repo as an ordinary gated test.

## Executable generation protocol kernel

The meta-audit above explains why another self-checking layer cannot erase the trusted
base.  The protocol below addresses a separate engineering question: how to make one
declared OOPTDD generation bounded, replayable, mutation-safe, and explicit about the
evidence it does not possess.  Its receipt self-hash is change detection, not one of the
external anchors described above.

Ouroboros v2 turns the informal “test the test, then bite the result” cycle into a
bounded protocol.  It is intentionally a **pure module**, not a daemon or workflow
engine.  The current implementation answers one narrow question:

> Given an immutable cycle snapshot and one typed event, what state and effect intents
> follow without inventing evidence?

The executable API is namespaced under `ooptdd.ouroboros`.

### The three layers of the theory

#### Philosophical: claims are not observations

OOPTDD begins from an epistemic asymmetry.  A producer can claim that it emitted an
event; only a read from the relevant territory can establish arrival.  Even that is not
automatically an independent verdict.  The protocol therefore keeps three vocabularies
disjoint:

- caller-selected monitor diagnostics: `sat | viol | pend`;
- observation semantics: `present | absent | inconclusive`;
- lifecycle policy: protocol phases and terminal reasons.

There is no generic truth-value conversion between them.  In particular, unreachable
territory is `inconclusive`, not `absent`, and a self-controlled reader cannot promote a
result to `external_verdict`.  Phase-specific predicates still apply: positive GREEN and
re-GREEN require both `outcome=green` and `observation=present`.  A run carrying
`outcome=inconclusive` or `observation=inconclusive` closes the generation as
`INCONCLUSIVE` (after restoration when a mutation is active).

The scalar `monitor` field is preserved only as caller-selected diagnostics.  Without a
check identity, gating/optional policy, aggregation threshold, and bounded-final marker,
one monitor value cannot determine the lifecycle.  An aggregate run may be GREEN while
one optional monitor is `viol`, or a bounded passing absence check remains `pend`.  A
future adapter may add typed per-check evidence; v2 does not infer it.

The protocol records a replayable closed-world claim: all declared obligations in one
locked generation satisfy its structural and semantic checks.  It does not establish
external truth, reducer authenticity, or that the declaration captured every fact about
the world.  It also does not establish scientific progress.

#### Mathematical: a bounded transition system with lineage

For snapshot set `S`, event set `E`, effect-intent sequence `I*`, and rejection set `R`,
the kernel is a total deterministic function:

```text
step : S × E → (S × I* × {accepted,replayed}) + (S × I* × R)
```

Its useful algebraic laws are:

1. **Determinism** — equal values produce equal results.
2. **Replay idempotence** — applying the same full cycle identity, `event_id`, and
   `intent_hash` again does not advance recorded history and returns the same effect IDs.
3. **Generation-scoped identity injectivity requirement** — within one generation, the same
   `event_id` with another intent is a conflict, never “last writer wins”.
4. **Material-lock conservation** — every phase run names one lock fingerprint.
5. **Restoration invariant** — terminal snapshots cannot retain an active mutation.
6. **Bite totality** — enumerated finding IDs and disposed finding IDs are equal sets,
   with exactly one disposition per ID.
7. **Generational monotonicity** — if a fix changes a bound material, generation `g`
   cannot complete as itself; it is superseded by `g+1`.  The successor can be
   constructed only after generation `g` has produced a validated receipt hash, which
   the successor identity binds.

The completion criterion is thus a local fixed point under the locked verifier and
scope, not a universal fixed point.  Changing the spec, verifier, source, or environment
creates a new problem and therefore a new generation.

#### Engineering: pure decision, caller-owned effects

The module contains no I/O.  A transition returns stable `EffectIntent` values.  A future
runner must commit its snapshot/event record before delivering effects and deduplicate by
`effect_id`.  Persistence, retries, timeouts, no-progress and total-invocation limits,
scheduling, and artifact reads remain ports owned by the caller.

Protocol identities use full SHA-256 values with algorithm, scope,
canonicalization, and schema version attached.  Raw file bytes and canonical JSON
objects have different functions and cannot be substituted.  Canonical protocol JSON:

- sorts object keys and removes insignificant whitespace;
- uses exact UTF-8 without Unicode normalization;
- accepts only string-keyed JSON values and interoperable integers;
- rejects floats, NaN/Infinity, `default=str`, and implicit object coercion.

Each event also carries `cycle_identity_sha256`, a domain-separated digest over cycle ID,
generation, and predecessor hash.  This prevents a valid trace from being relabelled as a
different generation after the fact.

### One generation

```text
INIT → SIZED → LOCKED → INITIAL_RED_CONFIRMED → GREEN_CONFIRMED
     → QUARANTINED → MUTATION_ACTIVE → NEGATIVE_RED_CONFIRMED
     → RESTORED → REGREEN_CONFIRMED → BITE_PENDING
     → COMPLETE | SUPERSEDED_BY_SUCCESSOR
```

The four run roles—initial RED, positive GREEN, negative RED, and restored
re-GREEN—must have distinct run IDs and artifact namespaces.  Each also names its exact
executed source: the negative run uses the mutated source, while the initial, positive,
and restored-positive runs use the locked baseline source.  The protocol does not require
artifact digests themselves to be pairwise unique: an adapter may legitimately encode
identical output bytes for different runs.  Positive runs must report a `present`
observation and carry a readback tier (`arrived` or stronger).  Those tiers and the oracle boundary are typed caller claims
until a store-specific adapter authenticates them; `external_verdict` additionally
requires the caller to declare a distinct, corroborated read authority.

During an active mutation, cancellation, timeout, exhausted ordinary-progress budget,
infrastructure uncertainty, or event-identity conflict first enters `RECOVERY_REQUIRED`. Restoration is
a safety action and remains allowed after the ordinary step budget is exhausted.  Only
then may the pending terminal reason take effect.

`max_steps` bounds ordinary progress transitions recorded in `steps_used`, while snapshot
`revision` tracks every journalled state mutation.  A reducer-detected fault may add one
non-counted handoff to `RECOVERY_REQUIRED`, followed by at most one non-counted safety
restoration; recovery rejects every other fresh event.  Thus a run started with
`CycleSnapshot.start` has at most `max_steps + 2` event records.  `max_generations` bounds
lineage depth.  Neither bound limits repeated rejected malformed/out-of-order calls or
exact replays, because those calls do not change history; therefore the pure kernel does
not itself guarantee caller termination.  Protocol faults that deliberately enter a
recovery or terminal state are journalled state transitions, not ordinary invalid-order
rejections.

### Receipt v2 and legacy input

`receipt_from_snapshot` accepts only `COMPLETE` or `SUPERSEDED_BY_SUCCESSOR`.  The receipt
covers the material lock, four phase-separated runs, mutation/restoration, complete Bite
dispositions, lineage, bounds, and replayable accepted event envelopes with phase edges.
Its self-hash is computed over the entire receipt except that single value; the exclusion
rule is narrow and deterministic.  This provides deterministic identity and change
detection, not signer authenticity or protection from an attacker who can rewrite both
content and hash.  Receipt validation establishes replayable structural and semantic
conformance, not external truth or reducer authenticity.

The event trace binds the declared generation metadata, and `successor_from_receipt`
freezes and validates its immediate predecessor before constructing the named successor.
The receipt alone still cannot prove that an arbitrary predecessor hash exists in an
authoritative store; a chain validator or authenticated receipt store must resolve that
external adjacency claim.

The historical `symposium-ooptdd-receipt/v1` lacks enough information to prove these
obligations.  `upcast_v1_receipt` therefore preserves the parsed source object without
inventing fields, but it is not a raw-byte-lossless container.  It always
returns `status: incomplete`, a null authoritative self-hash, and an explicit list of
missing proof.  It never guesses run identities, verifier/environment locks, a re-GREEN,
or Bite lineage.

### Artifacts

- `docs/ouroboros/module-decision.json` — module-vs-engine ADR and promotion falsifiers.
- `docs/ouroboros/fsm-spec.json` — machine-readable normal-flow and mutation-safety
  projection; reducer tests cover the explicitly scoped fault overlay.
- `docs/ouroboros/fsm-traces.json` — transition, guard-false, and invalid-event traces.
- `docs/ouroboros/fsm.mmd` — generated-view diagram; the JSON remains authoritative.
- `docs/ouroboros/bounded-execution-policy.json` — budgets, effects, and recovery boundary.
- `docs/ouroboros/verification.md` — claim/evidence matrix and remaining gaps.
- `docs/schema/ouroboros-receipt-v2.schema.json` — package-schema mirror.

The JSON Schema validates the portable closed shape and local field constraints.
`validate_receipt` remains mandatory for cross-field relations, integrity recomputation,
and reducer replay; JSON Schema alone is not a completion verifier.

### Deliberately deferred

There is no durable runner, snapshot journal, transactional outbox, mutation executor, or
store-specific adapter in this slice.  SEAL records completion or supersession but never
starts a successor; construction and launch are caller actions performed only after the
predecessor receipt is generated and validated.  These runtime capabilities become
justified only when the promotion gates in `module-decision.json` are met.  Until then,
claiming crash recovery, exactly-once external effects, authenticated evidence, or total
loop termination would exceed the implemented evidence.
