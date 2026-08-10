# Repository Contract

Instructions for coding agents and new contributors working in this repository.
This file is the single source; `CLAUDE.md` and any other tool file point here.

OOPTDD is an event-contract framework for deterministic evaluation and positive
arrival verification. Its thesis is *no silent pass*. A change that weakens a
gate to make a run go green contradicts the product.

## Commands

Python 3.10+ with [uv](https://docs.astral.sh/uv/).

- Install: `uv sync --locked --extra dev --extra otel`
- Fast validation: `python scripts/dev.py check` (~5s)
- Full local validation: `python scripts/dev.py verify` (minutes)
- Targeted test: `uv run --no-sync pytest -q tests/<file>.py`
- List what each stage runs: `python scripts/dev.py --list`

`check` is the inner-loop gate: architecture contract, Ouroboros readiness,
lint, and both mypy passes. Run it after every edit.

`verify` is the completion gate: `check` plus the full suite, the xdist
ship-once invariant, the example/adoption tests, the locked trajectory receipt,
and the backend matrix.

**`verify` is a subset of CI.** It cannot run the cross-OS/cross-Python matrix,
the installed-wheel extension jobs, the DeepEval and LakatoTree qualification
jobs, or the OpenObserve demos (those need Docker). A green `verify` is
necessary, not sufficient — see `.github/workflows/ci.yml`.

## Definition of Done

A task is complete only when:

1. Relevant tests were added or updated.
2. `python scripts/dev.py check` exits 0.
3. `python scripts/dev.py verify` exits 0.
4. No test, contract rule, type rule, lint rule, or gate was weakened.
5. Any new module is classified in the architecture contract (an unclassified
   module fails the build by design — `fail_on_unclassified_modules: true`).
6. The final diff was reviewed for unrelated changes.

Note the asymmetry: adding a scenario, contract, or test needs no approval.
**Changing an existing expected outcome, deleting a gate, relaxing a timeout, or
narrowing a matrix does.** Ask first.

## Architecture

The layering is machine-checked by `scripts/check_functional_architecture.py`
against `docs/architecture/functional-solid-contract.json`. That JSON is the
authority; this section only summarizes it. Layers, and what each may import:

| Layer | May depend on |
|---|---|
| `pure_core` (`engine.gate_*`, `ouroboros.{conformance,identity,model,ports,receipt,reducer}`) | `domain`, `pure_core` |
| `domain` (`domain.{model,ontology,ports,settings}`) | `domain` |
| `engine` (`engine.{gate,monitor,polling,verify}`) | `domain`, `engine`, `pure_core` |
| `protocol_api` (`ooptdd.ouroboros`) | `protocol_api`, `pure_core` |
| `adapters` (backends, probes, cli, config, bootstrap, …) — default layer | everything except `api` |
| `api` (`ooptdd`, `identity`, `sdk`) | everything |

Inside the 12 `pure_modules`, these are rejected:

- Importing `asyncio`, `datetime`, `logging`, `multiprocessing`, `os`,
  `pathlib`, `random`, `requests`, `secrets`, `socket`, `subprocess`,
  `tempfile`, `threading`, `time`, `urllib`, `uuid`.
- Calling `open`, `print`, `eval`, `exec`, `compile`, `input`, `breakpoint`,
  `__import__`.
- Non-frozen dataclasses, `global`/`nonlocal`, module-global mutation.

Two more boundaries the checker enforces:

- **Configuration.** Environment keys matching `OOPTDD_*` / `OTEL_EXPORTER_*`
  belong to `ooptdd.domain.settings`. Only `ooptdd.bootstrap` and
  `ooptdd.config` may read ambient environment.
- **Edge vocabulary.** The generic core must not name its consumers. Words like
  `pytest`, `tdd`, `gen-ai`, `trajectory` are forbidden in the inner layers —
  including comments and docstrings. Test frameworks and model domains are
  adapter concerns.

Files, environment variables, clocks, network queries, and plugin discovery
belong to outer adapters. See `SEMANTICS.md`.

## Coding Rules

- Preserve the three-valued verdict. `inconclusive` is never silently folded
  into `absent`. An unreachable or incomplete read is not a falsification.
- Expected failures are values, not exceptions. Return verdicts; do not throw
  across layers.
- Prefer frozen dataclasses, discriminated shapes, and exhaustive handling.
- No `Any`, unchecked casts, or `# type: ignore` without a reason on the line.
  `src/ooptdd/ouroboros` is `mypy --strict`.
- The architecture checker and the readiness checker are stdlib-only. Keep them
  that way — they must run before dependencies are installed.
- Prefer an existing pattern over a new abstraction. Do not add a project DSL
  or plugin layer until at least three real call sites demand it.
- Do not silently catch or discard errors.

## Workflow

1. Read the nearest tests and the closest existing implementation first.
2. State the intended behavior before editing.
3. Make the smallest coherent change.
4. Run the targeted test, then `python scripts/dev.py check`.
5. Before claiming completion, run `python scripts/dev.py verify`.
6. Report which commands you ran and what they printed. Do not report a green
   you did not observe.

If a gate fails for a reason you did not cause, say so and leave it alone
rather than fixing it into your diff.

## Execution Budget

An autonomous run operates under a declared budget, not an open loop:

1. Declare limits before starting: maximum tool calls, tokens, and wall-clock
   time. Exceeding a limit means stop and report — not retry.
2. No-progress stop: if the same gate fails three consecutive times for the
   same reason, stop and report the evidence. Do not grind against one wall.
3. Waiting is not computing. Check on external results (CI, hardware, a
   deploy) with one scheduled probe or a completion callback, not a retry loop.
4. Prefer a fresh session when the topic changes or the context has gone
   stale; a long-lived session resends its whole history every turn for no
   quality gain.
5. An agent's own green report is a claim, not a verification. Completion is
   accepted only after a second party — orchestrator or reviewer — re-runs
   `check`/`verify` independently.

Where a mechanical gate exists for one of these rules (runner budget,
auto-compaction limit, write-set deny), the gate is authoritative and the
corresponding prose above should be deleted. Prose that is only prose is a
rule that is not yet enforced.
