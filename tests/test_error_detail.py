"""Backend failures are attributed, malformed specs are clean errors (audit gap-08).

QueryResult carried no error field, so a 401, a DNS failure and an unconfigured store all
collapsed to an identical bare reachable=False. And load_gate let yaml.YAMLError (not a ValueError)
escape, so a malformed gate spec leaked as an uncaught traceback (exit 1) instead of the documented
clean error (exit 2). This pins the error attribution and the exit-code contract.
"""
import pytest

from ooptdd.backends.openobserve import OpenObserveBackend
from ooptdd.domain.ports import QueryResult
from ooptdd.engine.gate import load_gate

_BAD_YAML = "expect: [\n  - {event: a\n"  # unterminated flow sequence -> yaml.YAMLError


def _raising_opener(exc):
    def opener(req, timeout):
        raise exc
    return opener


def test_queryresult_error_defaults_to_none():
    assert QueryResult(reachable=True).error is None


def test_queryresult_error_carries_attribution():
    assert QueryResult(reachable=False, error="HTTPError: 401").error == "HTTPError: 401"


def test_a_failing_driver_attributes_the_error(monkeypatch):
    monkeypatch.setenv("OOPTDD_OO_URL", "http://oo.test:5080")
    monkeypatch.setenv("OOPTDD_OO_PASSWORD", "x")
    b = OpenObserveBackend(opener=_raising_opener(RuntimeError("boom-401")))
    r = b.query("c1", since_us=0, until_us=10 ** 18)
    assert r.reachable is False
    assert r.error and "boom-401" in r.error and "RuntimeError" in r.error


def test_malformed_gate_spec_raises_valueerror_not_yamlerror(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(_BAD_YAML)
    with pytest.raises(ValueError):
        load_gate(str(p))


def test_cli_malformed_gate_is_a_clean_exit_2(tmp_path):
    from ooptdd import cli
    p = tmp_path / "bad.yaml"
    p.write_text(_BAD_YAML)
    assert cli.main(["gate", str(p)]) == 2  # clean error (2), not an uncaught traceback (1)
