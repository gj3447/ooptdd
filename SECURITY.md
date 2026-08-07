# Security Policy

## Reporting a vulnerability

Please report security issues privately via GitHub's "Report a vulnerability"
(Security Advisories) on this repository, rather than opening a public issue.
We aim to acknowledge within a few business days.

## Design notes relevant to security

- **Secrets stay at the application boundary.** ooptdd never bakes URLs or
  credentials into code, config tables, or published artifacts. An outer
  composition or backend adapter supplies credentials, commonly from one captured
  environment snapshot; the evaluation kernel never reads ambient state.
- **Unreachable evidence stays inconclusive.** The framework returns an
  `inconclusive` verdict when a verification source cannot be observed. Callers and
  opt-in adapters independently decide how that evidence state affects a build,
  deployment, alert, or other policy.
- **Logs are not a redaction boundary.** Do not route secrets or PII through trace
  events for assertion; ooptdd treats security redaction as an explicit log-free
  zone (see `SEMANTICS.md`).
