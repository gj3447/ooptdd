# Case-study template (anonymized, wiring-accurate)

This template formalizes the fields [`case_studies.md`](case_studies.md) uses
implicitly, so a new case can be added — or an external consumer can submit one —
without re-deriving the conventions. Two rules are non-negotiable; everything
else is a field to fill in.

## Rule 1 — wiring honesty: say where the receipt runs

Every case study states its **enforcement locus** in the first paragraph, as one
of exactly three values:

| locus | meaning |
|---|---|
| **blocking-CI** | the receipt runs on every push in hosted CI and a RED fails the job |
| **local opt-in gate** | the receipt runs in local / pre-push gates, typically env-gated; hosted CI does not run it |
| **manual harness** | the receipt is re-runnable by a script a human invokes; nothing enforces it |

The ordering is blocking-CI > local opt-in gate > manual harness, and a case is
never rounded up: a doc that promotes "opt-in local gate" to "every CI run"
commits exactly the sin this library exists to catch. If different receipts in
the same consumer run at different loci, name each.

## Rule 2 — human review before publication

No case study is published (committed to a public branch, posted, or linked)
until a named human has read **the final prose and every attached artifact**.
The sanitizer script (`scripts/sanitize_case_study.py`, below) strips or hashes
the fields it knows about (`cid`, `service`, `emit_identity`,
`derived_identity`, host/url/endpoint keys) and any strings you configure — it
cannot know your product terms, internal codenames, or paths unless you
configure them. The sanitizer is a tool; the human review is the gate. Record
the reviewer sign-off in the PR, not in the published doc.

---

## Template

Copy the skeleton below into `docs/case_studies.md` (or a submission PR) and
fill every field. "Unknown" and "not measured" are acceptable values; an
omitted field is not.

```markdown
## <n>. <One-line anonymized system description> — <one-line integration shape>

<Opening paragraph: what the system is (anonymized), what is wired in, and the
enforcement locus (Rule 1) stated outright.>

**Scale.** <Tests collected (with the commit or date the count was taken at),
number of gates/receipts, event volume per run if known. Follow the repo's
verification-receipt convention: a count is stated with where it can be
re-derived, e.g. "N passed at commit X".>

**Backends.** <Which backend class(es) the receipts run against — and be exact
about memory vs external: the in-process store proves gate mechanics, not
arrival. Note write-only (ship-only, no strict verify possible), sampled, and
queryable backends separately. State which backend the *published* receipt
artifacts were produced against.>

**Enforcement locus.** <blocking-CI / local opt-in gate / manual harness, per
receipt if they differ. Name the env gating (e.g. `OOPTDD_BACKEND`,
`OOPTDD_VERIFY`) so "opt-in" is checkable, not vibes.>

**Vendoring / integration model.** <One of: vendored snapshot
(`scripts/vendor_ooptdd.py`, see `docs/MIGRATING_CONSUMERS.md`), package
dependency, or a native emitter in another language judged by this verifier in
a separate process. State it plainly — a vendored copy can drift, and that
caveat belongs in the case.>

**Verification posture.** <`warn` or `strict` (and where each applies); if
migrating, the stage per `docs/warn_to_strict.md`. Which enforcement wings are
on: `require_signature`, `require_corroboration`, `require_independent_store`,
`forbid_errors`; `confirm_rounds` if used.>

**Outcomes.** <What the receipts actually caught: silent ingest loss, false OK,
a forged/tampered receipt, a strength regression. If nothing has been caught
yet, say "nothing caught yet" — an armed gate with zero detections is a true
statement, not a failure of the case. If measured: inconclusive-incident rate,
and flake posture vs the prior timeout=fail behavior.>

**Evidence tier.** <The highest rung the headline receipt reaches:
local_pass / emitted / arrived / queryable_causal / external_verdict. A green
that only reaches `emitted` is loudly weak; name it anyway.>

**What this case does NOT demonstrate.** <Mandatory. E.g. "integration shape
at scale, not CI-resident enforcement" (case 1 in case_studies.md), or
"re-runnable, not continuously enforced" (case 3).>
```

## Redacted evidence artifacts (attach at least one, list all)

Each artifact is listed in the case study with what it is and how it was
redacted. Candidates, in rough order of evidentiary weight:

1. **Sanitized verdict receipt** — a real `session_finish` / `verify_gate` /
   `verify_trace` JSON run through `scripts/sanitize_case_study.py`, published
   as `case_study_receipt.json`. Must pass
   `sanitize_case_study.py --check <receipt> --sensitive <term>...` with the
   project's sensitive-term list before attaching.
2. **Redacted gate YAML snippet** — the spec is the judge, so showing (part of)
   it is the strongest "RED-first, in-repo as data" evidence. Redact event
   names only if they leak product terms; say so if you did.
3. **Strength fingerprint** — `ooptdd strength <spec> --write fp.json`
   (spec-derived, pure; usually contains nothing sensitive, but check). With a
   baseline, `--baseline` shows the weakening detector is armed.
4. **One sample inconclusive incident** — a real `?` verdict (store
   unreachable / truncated read) that did NOT fail the build. This is the
   three-valued honesty claim made concrete; a case study with only greens is
   less credible, not more.
5. **Mutation score** — `ooptdd mutate <spec> --events events.json
   [--min-score X]` output. Note the semantics honestly: exit 2 means the
   score is meaningless (no baseline green, or the drop-all canary survived —
   a gate that passes on an empty stream), and that is worth showing too.

## Publication checklist

- [ ] Enforcement locus stated in the first paragraph (Rule 1), per receipt if
      they differ; no claim rounds up.
- [ ] Every attached JSON artifact was produced by, or passed,
      `scripts/sanitize_case_study.py --check` with the project's
      sensitive-term list (a `--check` with zero configured terms is vacuous
      and exits 2 — configure the list).
- [ ] Final prose grepped for raw cids, hostnames, URLs, service names,
      usernames, internal repo paths.
- [ ] Evidence tier of the headline receipt named.
- [ ] "What this case does NOT demonstrate" filled in.
- [ ] Human reviewer sign-off recorded in the PR (Rule 2).
