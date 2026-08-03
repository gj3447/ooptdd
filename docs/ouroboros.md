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
