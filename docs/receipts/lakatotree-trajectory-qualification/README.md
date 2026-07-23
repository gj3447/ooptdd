# LakatoTree trajectory qualification

This directory is the frozen evidence nest for the DeepEval/Phoenix trajectory
programme.  It is deliberately split into preregistration, measurements,
evidence, judgment, and linked receipts so that the verdict is reproducible and
was not written by the implementer.

## Result

- Candidate: 27/27 deterministic cases matched, with 23/23 MemoryBackend
  ship/query readbacks non-empty.
- Baseline: 15/27 matched and left exactly three mechanism groups unresolved:
  `forbidden_tool_calls`, `matcher_composition`, and `phoenix_annotation`.
- Negative control: injecting a destructive call into the safe trajectory made
  the forbidden-call group fail, then a clean replay restored 27/27.
- Real DeepEval 4.0.7 probe: safe, destructive, and corrupt-input cases all
  produced the preregistered result (3/3).
- Canonical LakatoTree result: `progressive`, `delta=-3.0`, `kill=false`.

The judge source is `gj3447/lakatotree` at
`4525f0e447ebd25e8e827a9f071998ad8e15a094`.  CI checks out that exact commit
and replays `judge-response-v2.json` from `evidence-record-v2.json`.

## Honest failures caught during qualification

The first frozen programme targeted the legacy `lakatos.judge:judge`
entrypoint.  The current public engine moved the function to
`lakatos.verdict.judge:judge`; that run is retained as `v1-unjudged.json` and
was not patched around with a compatibility shim.  A corrected v2 programme
was preregistered before any v2 measurements.

The first pytest harness also reloaded package modules inside the test process,
splitting class and registry identities and breaking three otherwise unrelated
tests.  The harness now runs in an isolated subprocess.

## Independent engine health

The fresh LakatoTree checkout passed 1,821 tests (109 skipped), all five import
contracts, 235/235 Longinus bindings, 49/49 ooptdd receipt checks, and its 95%
coverage gate at 98.24%.

Mutation testing is not green: the repository currently resolves mutmut 3.6.0
although its script uses removed v2 flags.  Re-running with mutmut 2.5.1
completed with 121 killed, 124 survived, and one suspicious mutant (49.19%
excluding suspicious).  This is a LakatoTree hardening backlog, not evidence
against the preregistered ooptdd mechanism delta.

## Evidence boundary

MemoryBackend proves real ship/query/readback behavior within one process.  The
DeepEval probe is a separate real-library execution.  Phoenix uses an injected
recording opener, not a live Phoenix deployment.  No live ClickHouse,
VictoriaLogs, or Phoenix service was used in this qualification.
