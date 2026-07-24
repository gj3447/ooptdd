#!/usr/bin/env python3
"""Run the Tier-1 external-store arrival benchmark against a real OpenObserve.

Tier 0 (``ooptdd.benchmark``) proves gate mechanics deterministically; by design it
cannot prove that evidence arrived in an independent store. This runner is the Tier-1
judge: the five scenarios of
``docs/research/prom24_ooptdd_efficacy_20260723/D_measurement_plan.md`` §5, each repeated
under a fixed seed against the compose OpenObserve from
``examples/openobserve_demo/docker-compose.yml`` (image
``public.ecr.aws/zinclabs/openobserve:v0.14.7``).

    T1-loss-drop   shipper suppresses the required event          -> absent
    T1-loss-401    fire-and-forget shipper swallows a real 401    -> absent (never a fake green)
    T1-lag         ingest delayed <= declared visibility window   -> present, no early RED
    T1-outage      unroutable endpoint                            -> inconclusive (exit 2)
    T1-restore     401 negative, then fixed-auth replay           -> present (negative < restored)

Every rep gets a seed-derived ``variant_id`` plus its own stream/cid, records ship and
poll receipts plus an independent direct-SQL readback, and cleans its stream up
afterwards. Rollups report trial counts, Wilson 95% intervals, and the Tier-0
``pass_hat_k`` panel robustness. Canonical JSON is the evidence root; JUnit and Markdown
are pure projections and never re-judge a verdict.

Credentials are environment-only and are never written into artifacts. Defaults match the
compose file; override with ``OOPTDD_T1_OO_URL`` / ``OOPTDD_T1_OO_USER`` /
``OOPTDD_T1_OO_PASSWORD`` (or the ``--oo-*`` flags).

Exit ladder: 0 benchmark passed, 1 benchmark failed (an oracle miss is a RED), 2
infra/evidence-invalid (store down, lock/binding mismatch — never reported as a RED).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ooptdd import __version__
from ooptdd.backends.openobserve import OpenObserveBackend
from ooptdd.benchmark import _code_manifest as _packaged_code_manifest
from ooptdd.benchmark import canonical_json, pass_hat_k
from ooptdd.domain.ports import backend_caps
from ooptdd.engine.verify import verify_gate
from ooptdd.evidence_integrity import (
    EvidenceIntegrityError,
    measurement_environment,
    prospective_git_receipt,
    sha256_file,
    validate_registration_repository,
    validate_tier1_measurement_lock,
)
from ooptdd.reports import to_junit_xml, to_markdown

SCHEMA = "ooptdd-tier1-benchmark/v0"
BENCHMARK_ID = "ooptdd-tier1-arrival-v0"
BENCHMARK_VERSION = "0.1.0"
TIER = "tier1-external-store"
CLAIM_BOUNDARY = (
    "External-store arrival evidence over the five fixed T1 scenarios against one compose "
    "OpenObserve (v0.14.7) endpoint, with seeded repetitions and Wilson intervals. Not a "
    "claim about other backends, other versions, or a production trace distribution."
)
COMPOSE_IMAGE = "public.ecr.aws/zinclabs/openobserve:v0.14.7"

# Endpoint defaults mirror examples/openobserve_demo/docker-compose.yml; the password
# default is the compose root credential (a public, repo-committed demo secret), always
# overridable via env/flags and never recorded into any artifact.
DEFAULT_OO_URL = "http://localhost:5080"
DEFAULT_OO_USER = "root@example.com"
DEFAULT_OO_PASSWORD = "Complexpass#123"
URL_ENV = "OOPTDD_T1_OO_URL"
USER_ENV = "OOPTDD_T1_OO_USER"
PASSWORD_ENV = "OOPTDD_T1_OO_PASSWORD"
STALE_PASSWORD_ENV = "OOPTDD_T1_STALE_PASSWORD"
PROXY_URL_ENV = "OOPTDD_T1_PROXY_URL"
DEAD_URL_ENV = "OOPTDD_T1_DEAD_URL"
DEAD_URL = "http://127.0.0.1:59999"  # nothing listens here (demo_inconclusive shape)

REQUIRED_EVENT = "order.shipped"
SECONDARY_EVENT = "order.received"
#: The shipped OpenObserve driver's declared ingest-to-queryable blind window (ms). The
#: lag wing's proxy hold T is always < this (spec §2 M2a: T <= declared window).
DECLARED_VISIBILITY_MS = 5000

DEFAULT_SEED = 20260723
DEFAULT_REPETITIONS = 20

SCENARIO_DEFS = [
    {
        "id": "T1-loss-drop",
        "expected": "absent",
        "fault": "shipper suppresses the required event",
        "metric": "silent_loss_catch_rate",
    },
    {
        "id": "T1-loss-401",
        "expected": "absent",
        "fault": "fire-and-forget shipper swallows HTTP 401",
        "metric": "silent_loss_catch_rate",
    },
    {
        "id": "T1-lag",
        "expected": "present",
        "fault": "ingest POSTs held below the declared visibility window",
        "metric": "lag_false_red_rate",
    },
    {
        "id": "T1-outage",
        "expected": "inconclusive",
        "fault": "unroutable endpoint (dead port)",
        "metric": "outage_inconclusive_honesty_rate",
    },
    {
        "id": "T1-restore",
        "expected": "present",
        "fault": "401 negative, then fixed-auth replay of the same spec",
        "metric": "restore_rate",
    },
]

#: The frozen gate template every scenario judges (required event fixed, so the lock's
#: ``gate_spec_sha256`` binds a stable definition). ``require_independent_store`` is on
#: for the Tier-1 headline run (spec §7 tier-gaming mitigation): the OO driver declares
#: ``independent=True``, so no demotion fires — but a future dependent driver would be
#: caught instead of silently producing a "Tier-1" green.
GATE_SPEC_TEMPLATE = {
    "require_independent_store": True,
    "expect": [{"event": REQUIRED_EVENT, "op": ">=", "count": 1}],
}
EVENT_TEMPLATES = {
    "required": {"event": REQUIRED_EVENT},
    "secondary": {"event": SECONDARY_EVENT},
}
_ARRIVAL_KEYS = (
    "visibility_delay_ms",
    "waited_ms",
    "flushed",
    "extended_for_visibility",
    "confirm_rounds_run",
)


# ── deterministic identities ──────────────────────────────────────────────────


def variant_id(seed: int, scenario_id: str, repeat: int) -> str:
    """Seed-derived deterministic identity of one repetition (spec §4 run protocol)."""
    return hashlib.sha256(f"{seed}:{scenario_id}:{repeat}".encode()).hexdigest()[:16]


def rep_stream(variant: str) -> str:
    return f"ooptdd_tier1_{variant}"


def rep_cid(scenario_id: str, variant: str) -> str:
    return f"t1-{scenario_id.lower()}-{variant}"


def lag_hold_ms(variant: str) -> int:
    """Per-rep ingest hold T in [1000, 2499] ms — always < DECLARED_VISIBILITY_MS."""
    return 1000 + int(variant, 16) % 1500


def run_plan(seed: int, repetitions: int) -> list[dict]:
    """The dry-run artifact: every rep's deterministic identity, with no store contact."""
    plan = []
    for declared in SCENARIO_DEFS:
        for repeat in range(repetitions):
            variant = variant_id(seed, declared["id"], repeat)
            plan.append(
                {
                    "scenario_id": declared["id"],
                    "expected": declared["expected"],
                    "repeat": repeat,
                    "variant_id": variant,
                    "stream": rep_stream(variant),
                    "cid": rep_cid(declared["id"], variant),
                }
            )
    return plan


# ── statistics (pure) ─────────────────────────────────────────────────────────


def wilson_interval(successes: int, trials: int, *, z: float = 1.959963984540054) -> dict:
    """Wilson score interval for a binomial proportion (95% confidence by default).

    The D-plan (§5) requires the interval next to every Tier-1 rate — a point rate over
    20 trials without its uncertainty would overclaim. Exact at the edges: (n, n) has
    high 1.0 and (0, n) has low 0.0. Denominator-free inputs are rejected, never
    fabricated into a number.
    """
    if (
        isinstance(successes, bool)
        or isinstance(trials, bool)
        or not isinstance(successes, int)
        or not isinstance(trials, int)
    ):
        raise ValueError("wilson_interval needs integer counts")
    if trials < 1 or successes < 0 or successes > trials:
        raise ValueError("wilson_interval needs 0 <= successes <= trials and trials >= 1")
    z2 = z * z
    p = successes / trials
    denominator = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denominator
    margin = z * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials)) / denominator
    return {
        "low": max(0.0, center - margin),
        "high": min(1.0, center + margin),
        "z": z,
        "confidence": 0.95,
    }


def rate_rollup(events: list[bool], *, target: float) -> dict:
    """Rate + trial count + Wilson 95% + pass_hat_k panel robustness for one Tier-1 metric.

    ``events[i]`` is the counted outcome of trial i (a caught loss, a false RED, an honest
    inconclusive, a successful restore). The pass_hat_k "successes" are aligned with the
    target side: for a zero-target metric a success is the event *not* occurring. As with
    Tier 0, pass_hat_k is panel consistency over the frozen seeded panel — nothing more.
    """
    trials = len(events)
    if trials < 1:
        raise ValueError("rate_rollup needs at least one trial — never fabricate a rate")
    occurrences = sum(1 for flag in events if flag)
    successes = occurrences if target >= 0.5 else trials - occurrences
    k = min(8, trials)
    return {
        "value": occurrences / trials,
        "target": target,
        "trials": trials,
        "occurrences": occurrences,
        "wilson_95": wilson_interval(occurrences, trials),
        "pass_hat_k": {"k": k, "value": pass_hat_k(successes, trials, k)},
    }


# ── oracle + rollups (pure) ───────────────────────────────────────────────────


def sample_oracle_match(scenario_id: str, sample: dict) -> bool:
    """Recompute one rep's oracle from its raw receipt fields (never a stored flag).

    Each scenario's match requires the expected verdict AND its load-bearing receipts,
    so a verdict without its evidence (or a fake-green 401) cannot pass.
    """
    if scenario_id == "T1-loss-drop":
        # The secondary event must also be read back: if it were missing too, the trial
        # would show total loss, not the isolated drop fault the scenario injects.
        return (
            sample["observed"] == "absent"
            and sample["ship_receipt"]["attempted"] is True
            and sample["readback"]["reachable"] is True
            and sample["readback"]["required_rows"] == 0
            and sample["readback"]["rows"] >= 1
        )
    if scenario_id == "T1-loss-401":
        return (
            sample["observed"] == "absent"
            and sample["ship_receipt"]["http_status"] == 401
            and sample["readback"]["reachable"] is True
            and sample["readback"]["required_rows"] == 0
        )
    if scenario_id == "T1-lag":
        return (
            sample["observed"] == "present"
            and sample["arrival"]["extended_for_visibility"] is True
            and sample["intermediate_absent_verdicts"] == 0
        )
    if scenario_id == "T1-outage":
        return (
            sample["observed"] == "inconclusive"
            and sample["exit_code"] == 2
            and sample["junit_failures"] == 0
        )
    if scenario_id == "T1-restore":
        return (
            sample["negative"]["observed"] == "absent"
            and sample["observed"] == "present"
            and sample["chronology_hold"] is True
            and sample["restored"]["readback"]["required_rows"] >= 1
        )
    raise ValueError(f"unknown Tier-1 scenario {scenario_id!r}")


def scenario_rollup(scenario_id: str, expected: str, samples: list[dict]) -> dict:
    """Same rollup shape as the Tier-0 convention (rate + pass_hat_k over the panel)."""
    successes = sum(1 for sample in samples if sample["oracle_match"])
    attempts = len(samples)
    k = min(8, attempts)
    return {
        "id": scenario_id,
        "expected": expected,
        "attempts": attempts,
        "oracle_matches": successes,
        "oracle_match_rate": successes / attempts,
        "pass_hat_k": {"k": k, "value": pass_hat_k(successes, attempts, k)},
        "samples": samples,
    }


def build_metrics(rows: list[dict]) -> dict:
    """The four Tier-1 headline rates, recomputed from raw samples only."""
    by_id = {row["id"]: row for row in rows}
    loss = by_id["T1-loss-drop"]["samples"] + by_id["T1-loss-401"]["samples"]
    lag = by_id["T1-lag"]["samples"]
    outage = by_id["T1-outage"]["samples"]
    restore = by_id["T1-restore"]["samples"]
    return {
        "silent_loss_catch_rate": rate_rollup(
            [sample["observed"] == "absent" for sample in loss], target=1.0
        ),
        "lag_false_red_rate": rate_rollup(
            [bool(sample["false_red"]) for sample in lag], target=0.0
        ),
        "outage_inconclusive_honesty_rate": rate_rollup(
            [
                sample["observed"] == "inconclusive"
                and sample["exit_code"] == 2
                and sample["junit_failures"] == 0
                for sample in outage
            ],
            target=1.0,
        ),
        "restore_rate": rate_rollup(
            [
                sample["negative"]["observed"] == "absent"
                and sample["observed"] == "present"
                and sample["chronology_hold"] is True
                for sample in restore
            ],
            target=1.0,
        ),
    }


def benchmark_passed(rows: list[dict], metrics: dict) -> bool:
    """The §5 pass criteria: every scenario oracle held and every headline rate on target."""
    return (
        all(row["oracle_match_rate"] == 1.0 for row in rows)
        and metrics["silent_loss_catch_rate"]["value"] == 1.0
        and metrics["lag_false_red_rate"]["value"] == 0.0
        and metrics["outage_inconclusive_honesty_rate"]["value"] == 1.0
        and metrics["restore_rate"]["value"] == 1.0
    )


# ── projections (pure — verdicts come only from the engine) ───────────────────


def tier1_gate_result(result: dict) -> dict:
    """Honest scenario-oracle projection into the existing report renderer shape.

    Mirrors ``benchmark.benchmark_gate_result``: one check row per scenario; the outage
    row keeps ``inconclusive: true`` with ``passed`` from the oracle, so
    ``reports.to_junit_xml`` renders it ``<skipped>`` — never an ordinary pass and never
    ``<failure>`` (spec §5 mapping rule 1).
    """
    checks = [
        {
            "label": row["id"],
            "event": row["id"],
            "passed": row["oracle_match_rate"] == 1.0,
            "got": row["oracle_match_rate"],
            "want": 1.0,
            "observed": sorted({sample["observed"] for sample in row["samples"]}),
            "inconclusive": all(
                sample["observed"] == "inconclusive" for sample in row["samples"]
            ),
            "optional": False,
            "pending": False,
        }
        for row in result["scenarios"]
    ]
    return {
        "cid": f"{result['benchmark_id']}-seed{result['seed']}",
        "ok": result["passed"],
        "reachable": True,
        "complete": True,
        "checks": checks,
        "oracle": {"emit_identity": result["tier"]},
    }


def render_tier1_junit(result: dict) -> str:
    return to_junit_xml(tier1_gate_result(result), suite="ooptdd.tier1-benchmark")


def render_tier1_markdown(result: dict) -> str:
    header = (
        f"# ooptdd Tier-1 external-store benchmark — {'PASS' if result['passed'] else 'FAIL'}\n\n"
        f"> {result['claim_boundary']}\n\n"
    )
    return header + to_markdown(tier1_gate_result(result))


# ── provenance + lock binding (pure where possible) ───────────────────────────


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _sha256_value(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def tier1_provenance(*, runner_path: Path | None = None) -> dict:
    """The Tier-1 benchmark definition identity.

    Binds the packaged Python surface (the same code-manifest walker Tier 0 uses), the
    frozen scenario definitions, the gate/event templates, and this runner script — so a
    measurement lock pins exactly what produced a run.
    """
    code_manifest = _packaged_code_manifest()
    code_manifest_sha256 = _sha256_value(code_manifest)
    files = {
        "manifest": _sha256_value(SCENARIO_DEFS),
        "gate_spec": _sha256_value(GATE_SPEC_TEMPLATE),
        "events": _sha256_value(EVENT_TEMPLATES),
        "runner": sha256_file(runner_path or Path(__file__)),
    }
    return {
        "benchmark_definition_sha256": _sha256_value(
            {"code_manifest_sha256": code_manifest_sha256, **files}
        ),
        "code_manifest": code_manifest,
        "code_manifest_sha256": code_manifest_sha256,
        "files": files,
    }


def preflight_binding_mismatches(
    lock: dict, *, provenance: dict, deepeval_spec_sha256: str
) -> dict:
    """Locked-vs-observed Tier-1 definition hashes; an empty dict means the run is bound."""
    observed = {
        "benchmark_definition_sha256": provenance["benchmark_definition_sha256"],
        "code_manifest_sha256": provenance["code_manifest_sha256"],
        "manifest_sha256": provenance["files"]["manifest"],
        "gate_spec_sha256": provenance["files"]["gate_spec"],
        "events_sha256": provenance["files"]["events"],
        "runner_sha256": provenance["files"]["runner"],
        "deepeval_spec_sha256": deepeval_spec_sha256,
    }
    return {
        key: {"locked": lock.get(key), "observed": value}
        for key, value in observed.items()
        if lock.get(key) != value
    }


def binding_violations(
    lock: dict, *, head: str, dirty: bool, prereg_sha256: str
) -> list[str]:
    """Candidate/preregistration binding problems (empty = bound). Pure and fail-loud."""
    violations = []
    if head != lock.get("candidate_git_head") or dirty:
        violations.append("candidate source binding mismatch or dirty worktree")
    if prereg_sha256 != lock.get("preregistration_sha256"):
        violations.append("preregistration hash does not match the measurement lock")
    return violations


# ── small time/http helpers ───────────────────────────────────────────────────


def _now_us() -> int:
    return time.time_ns() // 1000


def _iso(now_us: int) -> str:
    moment = datetime.fromtimestamp(now_us / 1_000_000, tz=timezone.utc)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _basic_auth(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def _exit_code(verify_result: dict) -> int:
    """The CLI exit ladder (mirrors ``cli._cmd_verify``): 0 GREEN, 1 RED, 2 INFRA."""
    if verify_result["ok"]:
        return 0
    return 2 if verify_result["verdict"] == "inconclusive" else 1


def _junit_failure_count(gate_result: dict) -> int:
    root = ET.fromstring(to_junit_xml(gate_result, suite="ooptdd.tier1.scenario"))
    return len(list(root.iter("failure")))


def _arrival_stamp(result: dict) -> dict:
    return {key: result["arrival"][key] for key in _ARRIVAL_KEYS}


# ── the delaying fault point (stdlib only — no toxiproxy) ─────────────────────


class DelayedIngestProxy:
    """In-process forward proxy that holds ingest POSTs for ``hold_ms`` before relaying.

    The T1-lag fault point: only ``/_json`` ingest POSTs are delayed; every other request
    is relayed immediately. Each delayed request's measured hold is recorded for the
    receipt — the declared hold is never trusted as its own evidence.
    """

    def __init__(self, upstream_url: str, hold_ms: int, host: str = "127.0.0.1", port: int = 0):
        parsed = urllib.parse.urlsplit(upstream_url)
        if parsed.scheme != "http" or not parsed.hostname or not parsed.port:
            raise ValueError(
                f"DelayedIngestProxy needs an http://host:port URL, got {upstream_url!r}"
            )
        self._upstream_host = parsed.hostname
        self._upstream_port = parsed.port
        self.hold_ms = int(hold_ms)
        self.measured_holds_ms: list[float] = []
        self.relayed_requests = 0
        server = ThreadingHTTPServer((host, port), _DelayHandler)
        server.daemon_threads = True
        server.proxy_state = self
        self._server = server
        self.url = f"http://{host}:{server.server_address[1]}"
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)

    def start(self) -> DelayedIngestProxy:
        self._thread.start()
        return self

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> DelayedIngestProxy:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.close()


class _DelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # the proxy is a tool, not a log source
        pass

    def do_GET(self) -> None:
        self._relay()

    def do_PUT(self) -> None:
        self._relay()

    def do_DELETE(self) -> None:
        self._relay()

    def do_POST(self) -> None:
        self._relay()

    def _relay(self) -> None:
        state: DelayedIngestProxy = self.server.proxy_state  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        if self.command == "POST" and "/_json" in self.path:
            started = time.monotonic()
            time.sleep(state.hold_ms / 1000)
            state.measured_holds_ms.append((time.monotonic() - started) * 1000)
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in ("host", "content-length", "connection", "accept-encoding")
        }
        connection = http.client.HTTPConnection(
            state._upstream_host, state._upstream_port, timeout=30
        )
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            state.relayed_requests += 1
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() in ("transfer-encoding", "connection", "content-length"):
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        finally:
            connection.close()


# ── OpenObserve wiring (env-only credentials; never recorded) ─────────────────


@dataclass(frozen=True)
class Tier1Settings:
    """Resolved non-secret run configuration (the password lives only in the env)."""

    url: str
    org: str
    user: str
    timeout: float


class RecordingOpenObserveBackend(OpenObserveBackend):
    """OpenObserve driver + a poll log of every engine read.

    The lag wing's load-bearing evidence: each zero-required-row poll is a point where a
    naive retry loop would have concluded ABSENT — the blind-window guard is the only
    reason that verdict is not on record.
    """

    def __init__(self, *, required_event: str, **kwargs):
        super().__init__(**kwargs)
        self.required_event = required_event
        self.poll_log: list[dict] = []

    def query(self, cid: str, *, since_us: int, until_us: int):
        result = super().query(cid, since_us=since_us, until_us=until_us)
        self.poll_log.append(
            {
                "reachable": result.reachable,
                "complete": result.complete,
                "rows": len(result.events),
                "required_rows": sum(
                    1 for event in result.events if event.get("event") == self.required_event
                ),
            }
        )
        return result


def _driver(settings: Tier1Settings, stream: str, **overrides):
    options = {
        "stream": stream,
        "org": settings.org,
        "url_env": URL_ENV,
        "user_env": USER_ENV,
        "password_env": PASSWORD_ENV,
        "timeout": settings.timeout,
        **overrides,
    }
    return OpenObserveBackend(**options)


def _verifier(settings: Tier1Settings, stream: str, **overrides) -> RecordingOpenObserveBackend:
    options = {
        "stream": stream,
        "org": settings.org,
        "url_env": URL_ENV,
        "user_env": USER_ENV,
        "password_env": PASSWORD_ENV,
        "timeout": settings.timeout,
        "required_event": REQUIRED_EVENT,
        **overrides,
    }
    return RecordingOpenObserveBackend(**options)


def _event(cid: str, name: str) -> dict:
    return {"event": name, "cid": cid, "correlation_id": cid, "cycle_id": cid}


def _gate_spec(cid: str, *, anti_flap_guard: bool = False) -> dict:
    spec = {**GATE_SPEC_TEMPLATE, "cid": cid}
    if anti_flap_guard:
        # The negative wing makes the gate revocable-on-prefix, so verify_gate can never
        # early-settle: the poll loop is forced through the blind-window guard, which is
        # the mechanism T1-lag measures. Our shipped events carry no level, so the wing
        # itself passes.
        spec["forbid_errors"] = True
    return spec


def _ship_sync(shipper, events: list[dict]) -> dict:
    """A blocking ship with its own receipt (attempt, outcome, timing)."""
    started_us = _now_us()
    receipt = {
        "attempted": True,
        "events": [event.get("event") for event in events],
        "started_at": _iso(started_us),
        "http_status": None,
        "error": None,
    }
    try:
        shipper.ship(events)
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        receipt["http_status"] = getattr(exc, "code", None) or getattr(exc, "status", None)
    finished_us = _now_us()
    receipt["finished_at"] = _iso(finished_us)
    receipt["duration_ms"] = (finished_us - started_us) // 1000
    return receipt


def fire_and_forget_ship(shipper, events: list[dict]) -> dict:
    """The demo_silent_401 shape: fire-and-forget — the shipper swallows any ingest error
    and self-reports success; this wrapper records the HTTP status the app never saw."""
    receipt = {
        "attempted": True,
        "reported": "shipped OK",
        "events": [event.get("event") for event in events],
        "shipped_at": _iso(_now_us()),
        "http_status": None,
        "error": None,
    }
    try:
        shipper.ship(events)
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        receipt["http_status"] = getattr(exc, "code", None) or getattr(exc, "status", None)
    return receipt


def _password() -> str:
    return os.environ.get(PASSWORD_ENV, "")


def direct_readback(
    settings: Tier1Settings, stream: str, cid: str, *, since_us: int, until_us: int
) -> dict:
    """Independent store query: direct OO SQL over urllib — a second client path, not the
    driver under test. Returns counts only; full rows never enter the artifact."""
    safe_cid = cid.replace("'", "''")
    sql = f"SELECT * FROM {stream} WHERE cycle_id = '{safe_cid}'"
    body = json.dumps(
        {"query": {
            "sql": sql, "start_time": since_us, "end_time": until_us, "from": 0, "size": 1000,
        }}
    ).encode()
    request = urllib.request.Request(
        f"{settings.url}/api/{settings.org}/_search",
        data=body,
        method="POST",
        headers={
            "Authorization": _basic_auth(settings.user, _password()),
            "Content-Type": "application/json",
        },
    )
    queried_at = _now_us()
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout) as response:
            hits = json.loads(response.read().decode()).get("hits", [])
    except Exception as exc:
        return {
            "reachable": False,
            "rows": 0,
            "required_rows": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "queried_at": _iso(queried_at),
        }
    return {
        "reachable": True,
        "rows": len(hits),
        "required_rows": sum(1 for hit in hits if hit.get("event") == REQUIRED_EVENT),
        "error": None,
        "queried_at": _iso(queried_at),
    }


def cleanup_stream(settings: Tier1Settings, stream: str) -> dict:
    """Best-effort stream deletion; the outcome is recorded, never gated on."""
    request = urllib.request.Request(
        f"{settings.url}/api/{settings.org}/streams/{stream}?type=logs",
        method="DELETE",
        headers={"Authorization": _basic_auth(settings.user, _password())},
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout) as response:
            status = response.status
        ok = 200 <= status < 300
        return {"action": "delete_stream", "ok": ok, "http_status": status, "error": None}
    except urllib.error.HTTPError as exc:
        return {"action": "delete_stream", "ok": False, "http_status": exc.code, "error": str(exc)}
    except Exception as exc:
        return {
            "action": "delete_stream",
            "ok": False,
            "http_status": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _store_health(settings: Tier1Settings) -> bool:
    """Harness preflight: compose up or the run is INFRA (exit 2), not a benchmark RED."""
    request = urllib.request.Request(f"{settings.url}/healthz", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=min(5.0, settings.timeout)) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


# ── scenario runners (live store contact) ─────────────────────────────────────


def _sample_base(scenario_id: str, expected: str, *, seed: int, repeat: int) -> dict:
    variant = variant_id(seed, scenario_id, repeat)
    return {
        "repeat": repeat,
        "variant_id": variant,
        "stream": rep_stream(variant),
        "cid": rep_cid(scenario_id, variant),
        "expected": expected,
    }


def _run_loss_drop(settings: Tier1Settings, *, seed: int, repeat: int) -> dict:
    """Gate requires event B; the shipper ships only A (B suppressed). Expect ABSENT."""
    sample = _sample_base("T1-loss-drop", "absent", seed=seed, repeat=repeat)
    stream, cid = sample["stream"], sample["cid"]
    started_us = _now_us()
    shipper = _driver(settings, stream)
    ship_receipt = _ship_sync(shipper, [_event(cid, SECONDARY_EVENT)])
    ship_receipt["suppressed"] = [REQUIRED_EVENT]

    verifier = _verifier(settings, stream)
    result = verify_gate(verifier, cid, _gate_spec(cid), retries=2, delay=0.5)
    verified_us = _now_us()
    readback = direct_readback(
        settings, stream, cid, since_us=started_us - 600_000_000, until_us=verified_us + 300_000_000
    )
    cleanup = cleanup_stream(settings, stream)

    sample.update(
        {
            "observed": result["verdict"],
            "ship_receipt": ship_receipt,
            "verified_at": _iso(verified_us),
            "arrival": _arrival_stamp(result),
            "backend_caps": asdict(backend_caps(verifier)),
            "junit_failures": _junit_failure_count(result["gate"]),
            "readback": readback,
            "cleanup": cleanup,
            "case_parameters": {"variant_id": sample["variant_id"], "fault": "drop"},
        }
    )
    sample["oracle_match"] = sample_oracle_match("T1-loss-drop", sample)
    return sample


def _run_loss_401(settings: Tier1Settings, *, seed: int, repeat: int) -> dict:
    """Fire-and-forget shipper with a wrong password swallows the 401. Expect ABSENT."""
    sample = _sample_base("T1-loss-401", "absent", seed=seed, repeat=repeat)
    stream, cid = sample["stream"], sample["cid"]
    started_us = _now_us()
    os.environ[STALE_PASSWORD_ENV] = f"stale-credential-{sample['variant_id']}"
    broken_shipper = _driver(settings, stream, password_env=STALE_PASSWORD_ENV)
    ship_receipt = fire_and_forget_ship(broken_shipper, [_event(cid, REQUIRED_EVENT)])

    verifier = _verifier(settings, stream)
    result = verify_gate(verifier, cid, _gate_spec(cid), retries=2, delay=0.5)
    verified_us = _now_us()
    readback = direct_readback(
        settings, stream, cid, since_us=started_us - 600_000_000, until_us=verified_us + 300_000_000
    )
    cleanup = cleanup_stream(settings, stream)

    sample.update(
        {
            "observed": result["verdict"],
            "ship_receipt": ship_receipt,
            "verified_at": _iso(verified_us),
            "arrival": _arrival_stamp(result),
            "backend_caps": asdict(backend_caps(verifier)),
            "junit_failures": _junit_failure_count(result["gate"]),
            "readback": readback,
            "cleanup": cleanup,
            "case_parameters": {"variant_id": sample["variant_id"], "fault": "auth-401"},
        }
    )
    sample["oracle_match"] = sample_oracle_match("T1-loss-401", sample)
    return sample


def _run_lag(settings: Tier1Settings, *, seed: int, repeat: int) -> dict:
    """Ingest POSTs held T ms (T < declared window); a small poll budget exhausts inside
    the window. Expect final PRESENT via the visibility extension, and no early RED."""
    sample = _sample_base("T1-lag", "present", seed=seed, repeat=repeat)
    stream, cid = sample["stream"], sample["cid"]
    hold_ms = lag_hold_ms(sample["variant_id"])
    started_us = _now_us()
    with DelayedIngestProxy(settings.url, hold_ms) as proxy:
        os.environ[PROXY_URL_ENV] = proxy.url
        proxied_shipper = _driver(settings, stream, url_env=PROXY_URL_ENV)
        ship_receipt = _ship_sync(proxied_shipper, [_event(cid, REQUIRED_EVENT)])
        ship_receipt["proxy_hold_ms"] = hold_ms

        # A budget a naive retry loop would exhaust deep inside the 5000 ms blind window.
        verifier = _verifier(settings, stream)
        result = verify_gate(
            verifier, cid, _gate_spec(cid, anti_flap_guard=True), retries=2, delay=0.25
        )
        verified_us = _now_us()
        measured_holds = list(proxy.measured_holds_ms)
        relayed = proxy.relayed_requests

    readback = direct_readback(
        settings, stream, cid, since_us=started_us - 600_000_000, until_us=verified_us + 300_000_000
    )
    cleanup = cleanup_stream(settings, stream)

    poll_log = verifier.poll_log
    verdicts = ["pending"] * max(len(poll_log) - 1, 0) + [result["verdict"]]
    naive_absent_polls = sum(
        1
        for poll in poll_log
        if poll["reachable"] and poll["complete"] and poll["required_rows"] == 0
    )
    sample.update(
        {
            "observed": result["verdict"],
            "ship_receipt": ship_receipt,
            "verified_at": _iso(verified_us),
            "arrival": _arrival_stamp(result),
            "backend_caps": asdict(backend_caps(verifier)),
            "junit_failures": _junit_failure_count(result["gate"]),
            "poll_log": poll_log,
            "verdicts": verdicts,
            "intermediate_absent_verdicts": sum(1 for v in verdicts[:-1] if v == "absent"),
            "naive_absent_polls": naive_absent_polls,
            "false_red": result["verdict"] == "absent" and readback["required_rows"] > 0,
            "proxy": {
                "url": proxy.url,
                "upstream": settings.url,
                "hold_ms": hold_ms,
                "measured_holds_ms": measured_holds,
                "relayed_requests": relayed,
            },
            "readback": readback,
            "cleanup": cleanup,
            "case_parameters": {
                "variant_id": sample["variant_id"],
                "hold_ms": hold_ms,
                "declared_visibility_ms": DECLARED_VISIBILITY_MS,
            },
        }
    )
    sample["oracle_match"] = sample_oracle_match("T1-lag", sample)
    return sample


def _run_outage(settings: Tier1Settings, *, seed: int, repeat: int) -> dict:
    """Unroutable endpoint: expect INCONCLUSIVE + exit 2, and a <skipped> JUnit row."""
    sample = _sample_base("T1-outage", "inconclusive", seed=seed, repeat=repeat)
    cid = sample["cid"]
    os.environ[DEAD_URL_ENV] = DEAD_URL
    dead = _verifier(settings, "ooptdd_tier1_outage", url_env=DEAD_URL_ENV, timeout=2.0)
    result = verify_gate(dead, cid, _gate_spec(cid), retries=2, delay=0.25)
    verified_us = _now_us()

    sample.update(
        {
            "observed": result["verdict"],
            "endpoint": DEAD_URL,
            "verified_at": _iso(verified_us),
            "arrival": _arrival_stamp(result),
            "backend_caps": asdict(backend_caps(dead)),
            "junit_failures": _junit_failure_count(result["gate"]),
            "exit_code": _exit_code(result),
            "cleanup": {"action": "none", "ok": True, "detail": "no store writes by design"},
            "case_parameters": {"variant_id": sample["variant_id"], "fault": "unroutable"},
        }
    )
    sample["oracle_match"] = sample_oracle_match("T1-outage", sample)
    return sample


def _run_restore(settings: Tier1Settings, *, seed: int, repeat: int) -> dict:
    """Composite: the 401 negative first, then fixed auth replays the same spec.
    Expect the final verdict PRESENT, with the negative strictly before the restore."""
    sample = _sample_base("T1-restore", "present", seed=seed, repeat=repeat)
    stream, cid = sample["stream"], sample["cid"]
    started_us = _now_us()

    # Phase 1 — the negative: a stale credential drops the required event silently.
    os.environ[STALE_PASSWORD_ENV] = f"stale-credential-{sample['variant_id']}"
    broken_shipper = _driver(settings, stream, password_env=STALE_PASSWORD_ENV)
    negative_receipt = fire_and_forget_ship(broken_shipper, [_event(cid, REQUIRED_EVENT)])
    negative_verifier = _verifier(settings, stream)
    negative_result = verify_gate(negative_verifier, cid, _gate_spec(cid), retries=2, delay=0.5)
    negative_us = _now_us()

    # Phase 2 — restore: same spec, same cid, working credential.
    shipper = _driver(settings, stream)
    restored_receipt = _ship_sync(shipper, [_event(cid, REQUIRED_EVENT)])
    restored_verifier = _verifier(settings, stream)
    restored_result = verify_gate(restored_verifier, cid, _gate_spec(cid), retries=2, delay=0.5)
    restored_us = _now_us()
    readback = direct_readback(
        settings, stream, cid, since_us=started_us - 600_000_000, until_us=restored_us + 300_000_000
    )
    cleanup = cleanup_stream(settings, stream)

    sample.update(
        {
            "observed": restored_result["verdict"],
            "negative": {
                "observed": negative_result["verdict"],
                "measured_at": _iso(negative_us),
                "measured_at_us": negative_us,
                "ship_receipt": negative_receipt,
                "arrival": _arrival_stamp(negative_result),
            },
            "restored": {
                "observed": restored_result["verdict"],
                "measured_at": _iso(restored_us),
                "measured_at_us": restored_us,
                "ship_receipt": restored_receipt,
                "arrival": _arrival_stamp(restored_result),
                "readback": readback,
            },
            "chronology_hold": negative_us < restored_us,
            "backend_caps": asdict(backend_caps(restored_verifier)),
            "cleanup": cleanup,
            "case_parameters": {"variant_id": sample["variant_id"], "fault": "auth-401+restore"},
        }
    )
    sample["oracle_match"] = sample_oracle_match("T1-restore", sample)
    return sample


_SCENARIO_RUNNERS = {
    "T1-loss-drop": _run_loss_drop,
    "T1-loss-401": _run_loss_401,
    "T1-lag": _run_lag,
    "T1-outage": _run_outage,
    "T1-restore": _run_restore,
}


# ── live run assembly ─────────────────────────────────────────────────────────


def run_tier1_benchmark(
    settings: Tier1Settings, *, seed: int, repetitions: int, environment: dict
) -> dict:
    """Execute the fixed Tier-1 battery against the configured store and assemble the
    canonical result. Every aggregate is recomputed from raw per-rep receipts."""
    rows = []
    for declared in SCENARIO_DEFS:
        runner = _SCENARIO_RUNNERS[declared["id"]]
        samples = [runner(settings, seed=seed, repeat=repeat) for repeat in range(repetitions)]
        rows.append(scenario_rollup(declared["id"], declared["expected"], samples))
    metrics = build_metrics(rows)
    measured_us = _now_us()
    caps_probe = _verifier(settings, "ooptdd_tier1_caps")
    result = {
        "schema": SCHEMA,
        "benchmark_id": BENCHMARK_ID,
        "benchmark_version": BENCHMARK_VERSION,
        "ooptdd_version": __version__,
        "tier": TIER,
        "independent": True,
        "claim_boundary": CLAIM_BOUNDARY,
        "seed": seed,
        "repetitions": repetitions,
        "measured_at": _iso(measured_us),
        "environment": environment,
        "backend": {
            "driver": "OpenObserveBackend",
            "identity": settings.url,
            "org": settings.org,
            "image": COMPOSE_IMAGE,
            "caps": asdict(backend_caps(caps_probe)),
        },
        "provenance": tier1_provenance(),
        "scenarios": rows,
        "metrics": metrics,
        "passed": benchmark_passed(rows, metrics),
    }
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────


def _git(source_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source_root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _resolve_env(name: str, override: str | None, default: str) -> str:
    """CLI flag > existing env > built-in default; the resolved value goes back into the
    env so the env-only drivers pick it up. The password is resolved but never printed."""
    value = override or os.environ.get(name) or default
    os.environ[name] = value
    return value


def _validate_lock_binding(args, environment: dict):
    """Full prospective lock validation. Returns ``(lock_context, problems)``; any
    problem is an evidence-invalid/infra condition (exit 2), never a benchmark RED."""
    if (args.lock is None) != (args.preregistration is None):
        return None, ["--lock and --preregistration must be given together"]
    if args.lock is None:
        return None, []
    lock = validate_tier1_measurement_lock(json.loads(args.lock.read_text(encoding="utf-8")))
    prereg = json.loads(args.preregistration.read_text(encoding="utf-8"))
    prospective = prospective_git_receipt(args.lock, args.preregistration)
    validate_registration_repository(lock, prereg, prospective)
    problems = []
    if lock.get("environment") != environment:
        problems.append("measurement runtime does not match the frozen environment")
    source_root = args.source_root.resolve()
    try:
        head = _git(source_root, "rev-parse", "HEAD")
        dirty = bool(_git(source_root, "status", "--porcelain"))
    except (OSError, subprocess.CalledProcessError):
        head, dirty = "", True
    problems.extend(
        binding_violations(
            lock, head=head, dirty=dirty, prereg_sha256=sha256_file(args.preregistration)
        )
    )
    if prereg.get("registered_at") >= _iso(_now_us()):
        problems.append("preregistration is not earlier than measurement")
    deepeval_spec = (
        source_root / "docs" / "receipts" / "lakatotree-trajectory-qualification"
        / "qualification-spec-v2.json"
    )
    mismatches = preflight_binding_mismatches(
        lock, provenance=tier1_provenance(), deepeval_spec_sha256=sha256_file(deepeval_spec)
    )
    if mismatches:
        problems.append(f"preflight binding mismatch: {mismatches}")
    context = {
        "locked": True,
        "candidate_git_head": lock.get("candidate_git_head"),
        "preregistration_sha256": lock.get("preregistration_sha256"),
        "registration_repository": lock.get("registration_repository"),
        "prospective_registration": prospective,
    }
    return context, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--output", type=Path, help="canonical JSON output path")
    parser.add_argument("--junit-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    parser.add_argument("--oo-url", help=f"default ${URL_ENV} or {DEFAULT_OO_URL}")
    parser.add_argument("--oo-user", help=f"default ${USER_ENV} or {DEFAULT_OO_USER}")
    parser.add_argument(
        "--oo-password", help=f"default ${PASSWORD_ENV} or the compose root credential"
    )
    parser.add_argument("--oo-org", default="default")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--lock", type=Path, help="measurement lock JSON (with --preregistration)")
    parser.add_argument("--preregistration", type=Path, help="preregistration JSON (with --lock)")
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="skip store contact; print the seeded run plan and validate bindings only",
    )
    args = parser.parse_args(argv)
    if args.repetitions < 1:
        parser.error("--repetitions must be >= 1")

    url = _resolve_env(URL_ENV, args.oo_url, DEFAULT_OO_URL)
    user = _resolve_env(USER_ENV, args.oo_user, DEFAULT_OO_USER)
    _resolve_env(PASSWORD_ENV, args.oo_password, DEFAULT_OO_PASSWORD)
    settings = Tier1Settings(url=url, org=args.oo_org, user=user, timeout=args.timeout)
    environment = measurement_environment()

    try:
        lock_context, problems = _validate_lock_binding(args, environment)
    except (EvidenceIntegrityError, OSError) as exc:
        print(f"evidence-invalid: {exc}", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(f"evidence-invalid: {problem}", file=sys.stderr)
        return 2

    if args.dry_run:
        plan = run_plan(args.seed, args.repetitions)
        summary = {
            "schema": f"{SCHEMA}-dry-run",
            "tier": TIER,
            "seed": args.seed,
            "repetitions": args.repetitions,
            "scenarios": [declared["id"] for declared in SCENARIO_DEFS],
            "reps": len(plan),
            "unique_variants": len({rep["variant_id"] for rep in plan}),
            "unique_streams": len({rep["stream"] for rep in plan}),
            "lock": lock_context or {"locked": False},
            "plan": plan,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.output is None:
        parser.error("--output is required for a live run (use --dry-run to skip the store)")
    if not _store_health(settings):
        print(
            f"infra: OpenObserve unreachable at {settings.url} — start "
            "examples/openobserve_demo/docker-compose.yml (exit 2, not a benchmark RED)",
            file=sys.stderr,
        )
        return 2

    result = run_tier1_benchmark(
        settings, seed=args.seed, repetitions=args.repetitions, environment=environment
    )
    result["measurement_lock"] = lock_context or {"locked": False}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(result), encoding="utf-8")
    if args.junit_out:
        args.junit_out.parent.mkdir(parents=True, exist_ok=True)
        args.junit_out.write_text(render_tier1_junit(result), encoding="utf-8")
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(render_tier1_markdown(result), encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
