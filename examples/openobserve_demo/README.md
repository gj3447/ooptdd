# The founding incident, runnable in 60 seconds

ooptdd exists because of one production failure: **a silent `401` dropped log
ingest for 22 hours, and every "shipped OK" log line lied.** Return-value tests
stayed green the whole time. These three scripts reproduce that failure mode —
and the two honest verdicts around it — against a real OpenObserve.

```bash
docker compose up -d          # OpenObserve on :5080
export OOPTDD_OO_URL=http://localhost:5080
export OOPTDD_OO_USER=root@example.com
export OOPTDD_OO_PASSWORD='Complexpass#123'

python demo_green.py          # PRESENT  — events emitted, read back, confirmed
python demo_silent_401.py     # ABSENT   — "shipped OK" was a lie; the store is the judge
python demo_inconclusive.py   # INCONCLUSIVE — store down is NOT a failure verdict
```

Each script asserts its own expected verdict (the demo is itself a gate) and
exits 0 only when the demonstration held.

| script | what happens | verdict | why it matters |
|---|---|---|---|
| `demo_green.py` | emit → poll store → arrival confirmed | `present` | the happy path: proof, not trust |
| `demo_silent_401.py` | fire-and-forget shipper swallows a `401`; app logs "shipped OK" | `absent` | the founding incident: the self-report is green and wrong; only readback catches it |
| `demo_inconclusive.py` | store unreachable | `inconclusive` | LTL3's third value: "couldn't observe" is never demoted to "falsified" — an infra blip must not become a flaky RED |

Moving from observing to enforcing: see `docs/warn_to_strict.md`.
