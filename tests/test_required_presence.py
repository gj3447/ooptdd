"""Absence of the receipt substrate must be RED when required, SKIP-green otherwise (gap-03).

ooptdd is fail-open: receipts guard with ``pytest.importorskip("ooptdd...")``, so a missing /
rebuilt-away / fail-open-installed copy turns every receipt into a SKIP and a lane goes green
having verified nothing. The shipped canary (scripts/templates/conftest_ooptdd_required.py) makes
that a hard error when OOPTDD_REQUIRED is set. This drives the REAL canary artifact through a
subprocess pytest over a synthetic consumer, exercising all three states without un-installing
anything (absence is simulated with a module name that genuinely does not exist).
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

CANARY = (Path(__file__).resolve().parents[1]
          / "scripts" / "templates" / "conftest_ooptdd_required.py")
ABSENT = "ooptdd_substrate_that_is_not_installed_zzz"


def _consumer(tmp_path: Path, *, importorskip: str) -> Path:
    """A tiny consumer: the shipped canary as conftest.py, one importorskip'd receipt, and one
    ordinary test — so a skipped receipt sits inside an otherwise-green lane, exactly the real
    fail-open failure mode (the run is green; the receipt silently verified nothing)."""
    (tmp_path / "conftest.py").write_text(CANARY.read_text())
    (tmp_path / "test_receipt.py").write_text(textwrap.dedent(f"""
        import pytest
        pytest.importorskip({importorskip!r})

        def test_receipt():
            assert True
    """))
    (tmp_path / "test_other.py").write_text("def test_other():\n    assert True\n")
    return tmp_path


def _run(consumer: Path, required: str | None):
    env = os.environ.copy()
    # Isolate from the real installed ooptdd plugin so this measures the canary, not autoload.
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("OOPTDD_REQUIRED", None)
    if required is not None:
        env["OOPTDD_REQUIRED"] = required
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(consumer)],
        cwd=consumer, env=env, capture_output=True, text=True,
    )


def test_absent_and_not_required_skips_to_green(tmp_path):
    """The fail-open default (the defect this canary exists to fix): with the substrate absent and
    OOPTDD_REQUIRED unset, the receipt SKIPS while the lane stays green — it verified nothing, yet
    nothing goes red."""
    r = _run(_consumer(tmp_path, importorskip=ABSENT), required=None)
    assert r.returncode == 0, r.stdout + r.stderr
    out = (r.stdout + r.stderr).lower()
    assert "skipped" in out and "passed" in out


def test_absent_and_required_is_red(tmp_path):
    """The fix: declaring OOPTDD_REQUIRED turns the same absence into a loud collection error."""
    r = _run(_consumer(tmp_path, importorskip=ABSENT), required=ABSENT)
    assert r.returncode != 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "OOPTDD_REQUIRED" in out and ABSENT in out


def test_present_and_required_runs_the_receipt(tmp_path):
    """No false alarm: when the required module IS importable, the canary is a no-op and the
    receipt actually runs. ``os`` stands in for a present ooptdd (both are importable modules)."""
    r = _run(_consumer(tmp_path, importorskip="os"), required="os")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 passed" in (r.stdout + r.stderr)
