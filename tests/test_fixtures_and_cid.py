"""A cid seam on evaluate()/load_gate() + shipped pytest fixtures (audit gap-18).

Consumers hand-rolled the same setup every receipt: monkeypatch OOPTDD_CID + reset the
process-global memory store, because evaluate()/load_gate() had no cid= kwarg and the pytest11
plugin shipped no fixtures. This pins the cid seam and the opt-in fixtures.
"""
import os

import pytest

from ooptdd import MemoryBackend, evaluate, load_gate
from ooptdd.domain.model import correlation_keys

_EXPECT = [{"event": "a", "op": ">=", "count": 1}]


def test_evaluate_accepts_a_cid_kwarg_without_env(monkeypatch):
    monkeypatch.delenv("OOPTDD_CID", raising=False)
    b = MemoryBackend()
    b.ship([{**correlation_keys("k1"), "event": "a"}])
    res = evaluate(b, {"expect": _EXPECT}, cid="k1")  # no cid in the spec, no env
    assert res["ok"] is True and res["cid"] == "k1"


def test_evaluate_without_any_cid_still_raises(monkeypatch):
    """No-false-alarm: the new kwarg must not weaken the missing-cid safety."""
    monkeypatch.delenv("OOPTDD_CID", raising=False)
    with pytest.raises(ValueError):
        evaluate(MemoryBackend(), {"expect": _EXPECT})


def test_load_gate_cid_kwarg_overrides_the_spec(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("cid: from-file\nexpect:\n  - {event: a, op: '>=', count: 1}\n")
    assert load_gate(str(p), cid="override")["cid"] == "override"


def test_load_gate_without_cid_keeps_the_spec_value(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("cid: from-file\nexpect: []\n")
    assert load_gate(str(p))["cid"] == "from-file"


# ── the shipped fixtures (pytest11 plugin) — requesting them proves they exist + isolate ──
def test_ooptdd_cid_fixture_sets_env_and_round_trips(ooptdd_cid):
    assert os.getenv("OOPTDD_CID") == ooptdd_cid
    assert ooptdd_cid.startswith("test-")
    b = MemoryBackend()
    b.ship([{**correlation_keys(ooptdd_cid), "event": "a"}])
    assert evaluate(b, {"expect": _EXPECT}, cid=ooptdd_cid)["ok"] is True


def test_memory_reset_fixture_starts_from_a_clean_store(ooptdd_memory_reset):
    b = MemoryBackend()
    # nothing shipped yet under this cid, and the reset ran before us -> a gate finds nothing.
    assert evaluate(b, {"expect": _EXPECT}, cid="never-shipped")["ok"] is False
