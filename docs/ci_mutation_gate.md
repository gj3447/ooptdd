# Mutation score as a CI credibility gate

A green gate proves the expected events arrived. It does not prove the gate
could have noticed if they hadn't — or if they had arrived *wrong*. `ooptdd
mutate` measures exactly that second thing, and this page shows how to make the
measurement a blocking CI artifact:

```bash
ooptdd mutate gates/deploy.yaml --events baseline_events.json --min-score 0.8
```

The score is a **credibility number for the gate spec itself**: of the
deviations the gate *ought* to catch (derived from its own expectations), what
fraction actually turn it RED? Survivors are named blind spots. A team that
publishes this number alongside the gate verdict is saying "our judge has been
tested against planted failures" — the same reason
[warn → strict](warn_to_strict.md) demands a caught planted loss before
trusting a verifier's silence.

## What is measured

`mutation_report(events, spec)` (`src/ooptdd/mutation.py`) takes a *passing*
(events, gate) pair, derives labeled mutant event-lists from the gate's own
expectations, and re-runs the gate on each. Three operators:

| operator | mutation | a gate survives it when… |
|---|---|---|
| `drop:<x>` | remove every event satisfying a required expectation | it didn't actually require them |
| `corrupt:<x>.<field>` | for a `where`-constrained expectation, overwrite the matched field with a sentinel | it only checks existence, not the value |
| `inject_error` | append an ERROR-level record | it doesn't forbid errors (only derived when the spec does: `forbid_errors`, `OOPTDD_FORBID_ERRORS`, or an `absent`/`forbid` rule that covers ERROR/CRITICAL) |

`score = caught / n`, where `n` is the number of derived mutants. Mutants are
deduplicated by label, and a mutation that changes nothing (mutant list equals
the baseline) is silently skipped rather than counted as a free kill.

### What is deliberately NOT mutated

The mutant set is intentionally incomplete, and the exclusions are principled
(the docstring in `derive_mutations` states them; "exclusion beats a lying
score"):

- **`optional:`/`pending:` rules** — non-gating by design; a gate that ignores
  them is not exhibiting a blind spot.
- **Negative wings** (`absent`/`forbid`, and `forbidden_tools`) — dropping
  events can never fail them, so a drop-mutant proves nothing.
- **`ratioMetric`, `conforms`, `heartbeat`, `tool_calls`, `aggregate`** — no
  meaningful drop-mutant exists yet (a bare drop-everything is noise, not a
  discriminator). Real mutants for these (rename-tool / inject-forbidden /
  inflate-attr) are future work; until then they contribute **zero** mutants.
- **Reorder mutations** — out of scope: the in-memory backend stamps one
  timestamp per `ship`, so reordering can't be observed there.
- **Your code, and the engine** — the operators mutate the *event data list*,
  never an AST. This is not mutmut-style code mutation; there is no external
  test-runner subprocess. `evaluate()` is the library itself, run in-process.

Consequence: a gate built only from excluded predicate families derives `n=0`
mutants and its default score of `1.0` is a *vacuous* perfect, which the CLI
refuses to bless (see the exit ladder).

## The exit ladder

`_cmd_mutate` (`src/ooptdd/cli.py`) maps the report onto the CLI's shared
0/1/2 rungs, in this order:

| condition | exit | meaning |
|---|---|---|
| `baseline_green: false` | 2 | the inputs don't even pass the gate — the score is meaningless until fixed |
| `canary_survived: true` | 2 | the gate passed on an **empty** stream — vacuous by measurement (below) |
| `n == 0` with `--min-score` | 2 | no mutants derivable; score `1.0` is vacuous, never a clean "strong" |
| `n == 0` without `--min-score` | 0 | report emitted; nothing was graded, and nothing claimed |
| `score < min_score` | 1 | measured verdict: the gate let mutants through — too weak to block on |
| otherwise | 0 | measured pass at the declared threshold |

Note the asymmetry: exit 1 is the only *measured failure*; both exit-2 rungs
mean "this run graded nothing — fix the setup or the gate before reading the
number." A pipeline must not treat them interchangeably.

### The drop-all canary (exit 2)

Before you trust the score, `mutation_report` runs the gate once on an empty
event stream. If it still passes (`canary_survived: true`), the gate has **no
gating positive expectation at all** — vacuity proven by measurement, not
inferred. In ooptdd's pure data-list model that is the whole meaning of a
surviving drop-everything mutant; there is no external test harness whose
brokenness it could indicate (contrast mutmut's forced-fail check, which probes
a real subprocess runner).

The canary is **not counted into `score`** — it grades the gate's *shape*, not
a deviation the gate should catch — and it lands on the same INCONCLUSIVE rung
(exit 2) as the other vacuity signals. It is the dynamic cross-check of checks
you should already be running:

- `ooptdd lint <spec>` — the offline vacuity audit (VAC0–VAC4: empty `expect:`,
  all-optional, unjustified threshold, tautological counts, existence-only).
- `evaluate()` itself flags a gate whose every check is optional/pending as
  `vacuous` at run time — `ooptdd gate` prints `RED - vacuous gate` on that
  flag, and the JUnit renderer surfaces it as a synthetic failing testcase so
  the artifact can never read green on it. (`ooptdd strength` shows the same
  spec as `gating: 0`, score 0.)

Lint catches a vacuous gate at author time; the canary catches one that slipped
through anyway, at measurement time.

## A copy-pasteable GitHub Actions step

ooptdd is not yet on PyPI, so install from a checkout (vendor, submodule, or a
sibling clone — adjust the path):

```yaml
- name: Gate mutation score (credibility gate)
  run: |
    pip install ./ooptdd
    ooptdd mutate gates/deploy.yaml \
      --events ci/baseline_events.json \
      --min-score 0.8 \
      --json > mutation-report.json
    cat mutation-report.json

- name: Upload mutation report
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: mutation-report
    path: mutation-report.json
```

Wiring notes, honestly stated:

- `--json` puts the full report on stdout:
  `{baseline_green, mutations: [{mutation, caught}], survivors, score, n,
  canary_survived}`. Without it, the human one-liner (score, `n`, and the
  **survivors**, which are the actionable part) goes to stderr.
- GitHub Actions fails a step on *any* non-zero exit, so exit 1 (weak gate) and
  exit 2 (nothing graded) both block by default. That is a reasonable policy
  here — unlike a live `gate` run, an exit 2 from `mutate` is never a transient
  infra hold; it means the spec or the baseline inputs are broken and blocking
  is correct. If you still need to distinguish them, capture `$?` and branch,
  or read `canary_survived`/`n` from the JSON.
- `ci/baseline_events.json` is a checked-in event list, not a live readback.
  Keep it representative and keep it under review — a curated baseline that
  drifts from what production actually emits quietly narrows what the score
  means (see limits).
- Publish `score`, `n`, **and** `survivors` — a score without its `n` is not an
  artifact, it's a slogan.

## Composing with `gate --report junit|md`

The mutation run and the gate run are two different artifacts and should ship
side by side:

| artifact | command | what it proves |
|---|---|---|
| verdict | `ooptdd gate spec.yaml --report junit --report-out gate.xml` | these events actually arrived (readback from the store) |
| credibility | `ooptdd mutate spec.yaml --events baseline.json --json > mutation-report.json` | the gate would have noticed derived deviations |

The JUnit renderer (`src/ooptdd/reports.py`) makes the verdict a first-class CI
citizen: one `<testcase>` per check, INFRA renders as `skipped` (never
`failure` — `?` must not be demoted to `⊥`), and `--junit-inconclusive error`
is available for pipelines that must fail closed on an unverified run. The
markdown renderer is the PR-comment form and embeds the re-verify command so a
reviewer can independently re-check.

A verdict artifact answers "did it pass?"; the mutation artifact answers "would
passing have meant anything?" A PR page showing a green JUnit suite *plus* a
mutation report with `score: 1.0, n: 6, survivors: []` is a materially stronger
claim than the green suite alone — and one showing `survivors:
["corrupt:cycle.verdict"]` tells the reviewer exactly which wrong value the
gate would wave through today.

## Honest limits

- **The score grades the GATE, not the system.** A 1.0 means the spec would
  catch these derived deviations in these baseline events. It says nothing
  about whether the system is correct, and nothing about deviations outside the
  derived set.
- **The mutant set is derived from the gate's own expectations.** A gate that
  expects little generates few mutants — `n` shrinks, and each remaining mutant
  gets easier to catch. Always read `n` next to `score`.
- **Goodhart warning.** Once `--min-score` is a target, the cheapest way to
  raise the score is to *delete* the expectations whose mutants survive —
  fewer, easier mutants; higher score; weaker gate. The `n=0` guard blocks only
  the limit case. Pair the mutation gate with
  `ooptdd strength <spec> --baseline strength.json`, which turns exactly that
  move (dropping a `where`, marking a check optional, lowering a threshold)
  into a strength REGRESSION and exit 1. Mutation score and strength baseline
  cover each other's blind side; run both.
- **Evidence tier: in-memory only.** `mutation_report` runs every trial through
  `MemoryBackend` — it grades the spec's discriminating power over event lists,
  full stop. Lookback windows, paging, comparator behavior, and everything else
  about your production backend's readback path are *not* exercised. If your
  real gate runs against OpenObserve, say so next to the score: this is a
  pure-spec measurement, not evidence about the production wiring. (The live
  wiring has its own preflight: [warn → strict](warn_to_strict.md).)
- **Excluded families score nothing.** Trajectory/aggregate-only gates
  (`tool_calls`, `forbidden_tools`, `aggregate`, …) currently derive no
  mutants. For them the tool honestly reports `n=0` rather than inventing a
  number — which also means the mutation gate is currently **not** a useful
  credibility instrument for such gates. Use `lint`/`strength` there.
- **The baseline is author-chosen.** `--events` is whatever you checked in. A
  baseline that under-represents production traffic shapes narrows what "caught"
  means, silently.

## See also

- [warn → strict](warn_to_strict.md) — where the mutation gate sits in the
  enforcement ladder ("a verifier that has never caught a planted failure is
  uncorroborated").
- `ooptdd lint` / `ooptdd strength` — the static vacuity and weakening
  detectors this measurement cross-checks dynamically.
- `src/ooptdd/mutation.py` — operator derivation and the canary, with the
  design rationale inline.
