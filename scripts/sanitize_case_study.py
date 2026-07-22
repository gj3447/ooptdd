#!/usr/bin/env python3
"""Sanitize a real ooptdd verdict JSON into a publishable case-study receipt.

Input: a ``session_finish`` result (``{shipped, messages, fail_build}``), a
``verify_trace`` verdict (``{ok, verdict, session: {service, ...}, ...}``), a
``verify_gate`` result (``{ok, verdict, gate: {...}, ...}``), or a bare gate
result from ``evaluate_events`` — any JSON document, walked recursively.

Output: ``case_study_receipt.json`` with identity-bearing fields stripped of
their raw values. Verdicts, counts, and every honesty field (``ok``,
``verdict``, ``scope``, ``oracle`` counters, ``arrival``, ``attempts``) pass
through unchanged — the receipt stays judgeable, just not attributable.

What is anonymized (see ``HASHED_KEYS`` / ``CID_PATTERN``):

- values under identity-bearing keys: ``cid``, ``service``, ``emit_identity``,
  ``derived_identity``, ``identity``, ``host``, ``hostname``, ``endpoint``,
  ``url`` — replaced by a deterministic token ``anon-<sha256[:12]>``. Hashing
  (not blanking) on purpose: equal originals map to equal tokens, so relations
  like "the probe re-read the emit endpoint" (``derived_identity`` ==
  ``oracle.emit_identity``) survive anonymization and stay checkable.
- ``cid=<value>`` patterns inside free-text strings (``session_finish``
  messages embed the cid this way).
- any ``--sensitive`` / ``--sensitive-file`` terms, scrubbed case-insensitively
  from every string value.

Honesty limits: hashing a low-entropy value (a short service name) is
guessable by dictionary; dict KEYS are not rewritten (a sensitive term used as
a key fails the post-sanitize self-check instead of being silently published);
and this script cannot know product terms you did not configure. The
human-review-before-publication gate (docs/case_study_template.md, Rule 2)
applies regardless of a clean ``--check``.

Deterministic (same input + same flags -> byte-identical output; no timestamps,
no randomness), stdlib-only, no network.

Exit codes (the repo's exit ladder): 0 clean, 1 leak detected (``--check``
found a configured term, or sanitization could not remove one), 2 unusable
input / vacuous check (``--check`` with zero configured terms can never fail,
so it proves nothing).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

#: Keys whose STRING values are identity-bearing and get replaced by ``anon-<hash>``.
#: ``cid``/``service``/``emit_identity``/``derived_identity`` are the fields the
#: engine actually emits (engine/verify.py, engine/gate.py); the host/url/endpoint
#: aliases cover meta blocks consumers attach themselves.
HASHED_KEYS = frozenset({
    "cid", "service", "emit_identity", "derived_identity", "identity",
    "host", "hostname", "endpoint", "url",
})

#: ``session_finish``/``verify_policy`` messages embed the cid as ``cid=<value>``
#: inside free text — scrub it there too, not only under the ``cid`` key.
CID_PATTERN = re.compile(r"cid=([A-Za-z0-9._:\-]+)")

SANITIZER_VERSION = 1


def anon_token(value: str, salt: str = "") -> str:
    """Deterministic anonymization token: equal inputs -> equal tokens."""
    digest = hashlib.sha256((salt + value).encode("utf-8")).hexdigest()[:12]
    return f"anon-{digest}"


def _scrub_string(s: str, sensitive: list[str], salt: str) -> str:
    s = CID_PATTERN.sub(lambda m: "cid=" + anon_token(m.group(1), salt), s)
    # longest-first so "acme-corp-eu" is consumed before "acme-corp" can split it
    for term in sorted((t for t in sensitive if t), key=len, reverse=True):
        s = re.sub(re.escape(term), anon_token(term, salt), s, flags=re.IGNORECASE)
    return s


def sanitize(node, sensitive: list[str] | None = None, salt: str = ""):
    """Recursively anonymize ``node`` (pure; the input object is not mutated)."""
    sensitive = sensitive or []
    if isinstance(node, dict):
        return {
            k: (anon_token(v, salt) if k in HASHED_KEYS and isinstance(v, str)
                else sanitize(v, sensitive, salt))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [sanitize(v, sensitive, salt) for v in node]
    if isinstance(node, str):
        return _scrub_string(node, sensitive, salt)
    return node  # numbers / bools / None: verdicts and counts pass through untouched


def find_leaks(doc, sensitive: list[str]) -> list[str]:
    """Which configured terms still appear ANYWHERE in ``doc`` (keys included)?
    Serialized case-insensitive substring search — deliberately blunt: a term
    surviving inside a key name or a nested string is still a leak."""
    text = json.dumps(doc, ensure_ascii=False).lower()
    return sorted({t for t in sensitive if t and t.lower() in text})


def _load_sensitive(args) -> list[str] | None:
    """Collect --sensitive terms + --sensitive-file lines (# comments, blanks
    skipped). None on a file error (caller exits 2)."""
    terms = list(args.sensitive)
    if args.sensitive_file:
        try:
            with open(args.sensitive_file, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        terms.append(line)
        except OSError as exc:
            print(f"ERROR - cannot read --sensitive-file: {exc}", file=sys.stderr)
            return None
    return terms


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sanitize_case_study.py",
        description="Strip/hash identity fields from an ooptdd verdict JSON for "
                    "publication as a case-study receipt.",
    )
    p.add_argument("input", help="verdict JSON (session_finish / verify_gate / "
                                 "verify_trace / gate result)")
    p.add_argument("-o", "--output", default="case_study_receipt.json",
                   help="output path (default: %(default)s)")
    p.add_argument("--sensitive", action="append", default=[], metavar="TERM",
                   help="a string that must not survive; repeatable")
    p.add_argument("--sensitive-file", metavar="PATH",
                   help="file with one sensitive term per line (# comments ok)")
    p.add_argument("--salt", default="",
                   help="optional hash salt; same salt -> same tokens (default: none, "
                        "fully deterministic)")
    p.add_argument("--check", action="store_true",
                   help="do not sanitize; verify the input contains none of the "
                        "configured sensitive terms (exit 1 on a leak, 2 if no "
                        "terms are configured — a check that cannot fail is vacuous)")
    args = p.parse_args(argv)

    sensitive = _load_sensitive(args)
    if sensitive is None:
        return 2
    try:
        with open(args.input, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR - cannot load {args.input}: {exc}", file=sys.stderr)
        return 2

    if args.check:
        if not sensitive:
            print("INCONCLUSIVE - --check with zero configured sensitive terms is "
                  "vacuous (it can never fail); pass --sensitive/--sensitive-file",
                  file=sys.stderr)
            return 2
        leaks = find_leaks(doc, sensitive)
        if leaks:
            print(f"LEAK - {len(leaks)} configured term(s) survive in {args.input}: "
                  + ", ".join(leaks), file=sys.stderr)
            return 1
        print(f"CLEAN - none of the {len(sensitive)} configured term(s) appear in "
              f"{args.input}", file=sys.stderr)
        return 0

    receipt = sanitize(doc, sensitive, args.salt)
    if isinstance(receipt, dict):
        receipt["_sanitizer"] = {
            "tool": "scripts/sanitize_case_study.py",
            "version": SANITIZER_VERSION,
            "hashed_keys": sorted(HASHED_KEYS),
            "sensitive_terms_scrubbed": len(sensitive),
            "salted": bool(args.salt),
        }
    # Self-check: value scrubbing cannot rewrite dict KEYS — if a configured term
    # survives (e.g. as a key name), refuse to write rather than publish a leak.
    leaks = find_leaks(receipt, sensitive)
    if leaks:
        print(f"LEAK - sanitization left {len(leaks)} configured term(s) in the "
              f"output (likely inside a dict KEY, which is never rewritten): "
              + ", ".join(leaks) + " - output NOT written; redact manually",
              file=sys.stderr)
        return 1
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"OK - sanitized receipt written to {args.output} "
          f"({len(sensitive)} sensitive term(s) scrubbed); human review before "
          "publication still applies (docs/case_study_template.md Rule 2)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
