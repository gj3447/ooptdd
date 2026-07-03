"""Boolean config parsing is one strict convention, and forbid_errors is provenanced (gap-22).

Two verdict-affecting defects: (1) OOPTDD_ENABLED=off/no/FALSE ENABLED a network backend under
the `auto` default because the check was a case-sensitive membership test against {"","0","false"}
only; (2) forbid_errors changed the gate verdict but — unlike require_corroboration — was never
stamped into the result, so a judge could not tell whether the negative wing was enforced. This
pins one strict parse_bool and the forbid_errors provenance stamp.
"""
import warnings

import pytest

from ooptdd.config import Settings, parse_bool
from ooptdd.engine.gate import evaluate_events


# ── parse_bool: one strict, case/space-insensitive convention ────────────────────────
@pytest.mark.parametrize("v", ["1", "true", "TRUE", " yes ", "On"])
def test_parse_bool_recognizes_truthy(v):
    assert parse_bool(v, default=False) is True


@pytest.mark.parametrize("v", ["0", "false", "FALSE", " no ", "Off"])
def test_parse_bool_recognizes_falsey(v):
    # default=True proves these are recognized as FALSE, not just falling through to the default.
    assert parse_bool(v, default=True) is False


@pytest.mark.parametrize("v", ["", None])
def test_parse_bool_empty_returns_default_without_warning(v):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert parse_bool(v, default=True) is True
        assert parse_bool(v, default=False) is False


def test_parse_bool_unrecognized_warns_and_uses_default():
    with pytest.warns(UserWarning):
        assert parse_bool("maybe", default=True) is True
    with pytest.warns(UserWarning):
        assert parse_bool("maybe", default=False) is False


# ── is_enabled: OOPTDD_ENABLED must be honored consistently under auto ────────────────
@pytest.mark.parametrize("val", ["off", "no", "FALSE", "0"])
def test_enabled_off_disables_a_network_backend_under_auto(monkeypatch, val):
    monkeypatch.setenv("OOPTDD_ENABLED", val)
    assert Settings(backend="clickhouse", enabled="auto").is_enabled() is False


@pytest.mark.parametrize("val", ["on", "yes", "TRUE", "1"])
def test_enabled_on_enables_a_network_backend_under_auto(monkeypatch, val):
    monkeypatch.setenv("OOPTDD_ENABLED", val)
    assert Settings(backend="clickhouse", enabled="auto").is_enabled() is True


def test_memory_backend_is_enabled_under_auto_regardless(monkeypatch):
    monkeypatch.delenv("OOPTDD_ENABLED", raising=False)
    assert Settings(backend="memory", enabled="auto").is_enabled() is True


def test_explicit_disable_wins_even_for_memory():
    """Permanent guard: an explicit off must beat the memory auto-on — kills a fix that only
    special-cases the env and forgets the explicit flag."""
    assert Settings(backend="memory", enabled="off").is_enabled() is False


# ── forbid_errors provenance stamp ───────────────────────────────────────────────────
def _eval(spec):
    return evaluate_events(spec, [{"event": "a", "_timestamp": 1}], reachable=True, cid="c")


def test_forbid_errors_is_stamped_into_the_result():
    on = _eval({"cid": "c", "expect": [{"event": "a", "op": ">=", "count": 1}],
               "forbid_errors": True})
    off = _eval({"cid": "c", "expect": [{"event": "a", "op": ">=", "count": 1}],
                 "forbid_errors": False})
    assert on["oracle"]["forbid_errors"] is True
    assert off["oracle"]["forbid_errors"] is False
