# Security Policy

## Reporting a vulnerability

Please report security issues privately via GitHub's "Report a vulnerability"
(Security Advisories) on this repository, rather than opening a public issue.
We aim to acknowledge within a few business days.

## Design notes relevant to security

- **Secrets are environment-only.** ooptdd never bakes URLs or credentials into
  code, config tables, or published artifacts. A backend that needs auth reads it
  from named environment variables at call time.
- **Fail-open by default.** A verification backend being unreachable yields an
  `inconclusive` verdict and never fails the build, so an outage can't be used to
  mask test results — but it also won't silently turn observability into a hard
  gate unless you opt into `strict`.
- **Logs are not a redaction boundary.** Do not route secrets or PII through trace
  events for assertion; ooptdd treats security redaction as an explicit log-free
  zone (see `METHODOLOGY.md`).
