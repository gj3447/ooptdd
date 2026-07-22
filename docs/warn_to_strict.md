# warn → strict: turning observation into enforcement

`[tool.ooptdd] verify = "off" | "warn" | "strict"` controls what happens when the
pytest session's own trace fails positive verification. Teams should climb this
ladder deliberately — flipping straight to `strict` against an unproven store
converts every infra hiccup into a red build, and teams learn to ignore gates
that cry wolf.

## The ladder

1. **`off`** — events still ship; nothing is verified. Only for bootstrapping
   the emit path.
2. **`warn`** (start here) — the verifier polls the store and *reports* absence
   without failing the build. Run at least a week of CI here and watch for:
   - `INCONCLUSIVE` noise → your store or network is not reliable enough yet
     to be a judge; fix that first (an unreachable judge is not a judge).
   - `absent` findings → real ingest losses. Every one of these is the founding
     incident happening to you in miniature. Fix the pipe, not the gate.
3. **`strict`** — absence fails the session. Flip only after the preflight
   below has been green for a representative period.

## Preflight before flipping strict

```bash
ooptdd backends doctor --backend openobserve   # reachable? WHY not, if not (401 vs DNS)
ooptdd verify <recent-cid> --backend openobserve   # a known-good cid reads back
python examples/openobserve_demo/demo_silent_401.py  # the negative wing actually catches loss
```

The third line matters most: prove the gate can SEE a loss before trusting its
silence. A verifier that has never caught a planted failure is uncorroborated
(mutation-test your gates for the same reason: `ooptdd mutate --min-score`).

## Semantics that keep strict mode livable

- INCONCLUSIVE (store unreachable / truncated read) exits 2, not 1 — CI should
  *hold*, not fail, on infra blindness. Wire your pipeline accordingly
  (`allow_failure` on exit 2, or a retry stage).
- `optional: true` checks never gate — use them to stage new expectations in
  production gates before promoting them to gating.
- `forbid_errors` defaults from `OOPTDD_FORBID_ERRORS`; grant exemptions per
  known-benign event via `allow_errors`, never by turning the wing off.
