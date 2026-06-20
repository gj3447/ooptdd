import pytest

from ooptdd.backends import memory_reset

pytest_plugins = ["pytester"]

# Ambient OOPTDD_* env that, if set, would leak into the tests. Running the suite with real-store
# config (to dogfood the WHOLE suite against a real OpenObserve: OOPTDD_BACKEND=openobserve /
# OOPTDD_OO_* / OOPTDD_VERIFY=strict / a fixed OOPTDD_CID) otherwise breaks tests that read it —
# the CLI tests pick up the backend (-> unreachable, exit 2), and the pytester self-tests inherit
# it in their inner run (env-wins overrides their ini; a fixed cid collides in the shared store).
# Backend tests set their own *_URL via monkeypatch, so clearing here is safe — their setenv runs
# in the test body, after this autouse fixture, and wins; monkeypatch restores everything at
# teardown, so a parallel real-store dogfood of the outer session (configured at session start)
# is unaffected.
_AMBIENT_OOPTDD_ENV = (
    "OOPTDD_BACKEND", "OOPTDD_SERVICE", "OOPTDD_VERIFY", "OOPTDD_ENABLED", "OOPTDD_CID",
    "OOPTDD_SIGNING_KEY", "OOPTDD_REQUIRE_SIGNATURE",
    "OOPTDD_OO_URL", "OOPTDD_OO_USER", "OOPTDD_OO_PASSWORD", "OOPTDD_OO_ORG",
    "OOPTDD_CH_URL", "OOPTDD_VL_URL",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Make the suite hermetic against ambient OOPTDD_* env so it stays GREEN even when run with
    real-store config set (a full-suite dogfood). A test's own monkeypatch.setenv still wins."""
    for name in _AMBIENT_OOPTDD_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _clean_memory_store():
    memory_reset()
    yield
    memory_reset()
