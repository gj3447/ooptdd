"""Optional receipt canary — make ooptdd's ABSENCE a hard error, not a silent skip.

By design ooptdd is fail-open: the pytest plugin only auto-loads if importable, and receipt
tests guard themselves with ``pytest.importorskip("ooptdd...")``. So a missing vendored copy, a
fail-open CI install, or a ``.venv`` rebuilt without ooptdd turns EVERY receipt into a SKIP —
and a lane that skipped every receipt reports green having verified nothing. There is no way,
out of the box, to say "no, this lane MUST have receipts."

This is that way. Copy it next to your receipts (or merge the body into an existing
``conftest.py``). It is a no-op unless ``OOPTDD_REQUIRED`` is set, so it is safe to commit in
every environment; set the env only on the lanes that must not skip:

    OOPTDD_REQUIRED=1                              # require `ooptdd`
    OOPTDD_REQUIRED=ooptdd.backends,ooptdd_loop    # require exactly these modules

When set and any named module is not importable, this aborts collection with a loud error
instead of letting the run skip to green. Pure stdlib, offline.
"""
import importlib
import os

_FALSEY = {"", "0", "false", "no", "off"}
_TRUTHY = {"1", "true", "yes", "on"}


def _required_modules() -> list[str]:
    raw = os.getenv("OOPTDD_REQUIRED", "").strip()
    if raw.lower() in _FALSEY:
        return []
    if raw.lower() in _TRUTHY:
        return ["ooptdd"]
    return [m.strip() for m in raw.split(",") if m.strip()]


def _check_required_present() -> None:
    missing = []
    for mod in _required_modules():
        try:
            importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001 — any import failure means "not really present"
            missing.append(f"{mod} ({type(exc).__name__}: {exc})")
    if missing:
        raise RuntimeError(
            "OOPTDD_REQUIRED is set but the receipt substrate is not importable: "
            + "; ".join(missing)
            + ". This lane must verify receipts; a missing / rebuilt-away / fail-open-installed "
            "ooptdd would otherwise turn every importorskip'd receipt into a silent SKIP (green "
            "having verified nothing). Re-install or re-vendor ooptdd, or unset OOPTDD_REQUIRED "
            "on lanes where receipts are genuinely optional."
        )


# Runs at conftest import time (before collection), so absence aborts the session.
_check_required_present()
