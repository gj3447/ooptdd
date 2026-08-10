@AGENTS.md

## Claude-specific

- Use plan mode for changes that cross a layer boundary in
  `docs/architecture/functional-solid-contract.json`.
- `scripts/dev.py check` is cheap (~5s). Run it after every edit rather than
  batching validation to the end.
