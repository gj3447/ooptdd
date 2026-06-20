"""Plugin-level behaviour, exercised with pytest's own Pytester.

Hermeticity against ambient OOPTDD_* env (so these pytester self-tests survive a full-suite
real-store dogfood) is provided by the autouse ``_hermetic_env`` fixture in conftest.py.
"""


def test_plugin_ships_and_confirms_arrival(pytester):
    pytester.makepyfile("def test_ok():\n    assert True\n")
    pytester.makeini("[pytest]\nooptdd_backend = memory\nooptdd_enabled = 1\n")
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*[ooptdd]*arrival confirmed*"])


def test_plugin_is_a_true_noop_when_disabled(pytester):
    pytester.makepyfile("def test_ok():\n    assert True\n")
    result = pytester.runpytest("--no-ooptdd")
    result.assert_outcomes(passed=1)
    assert "[ooptdd]" not in result.stdout.str()


def test_strict_inconclusive_never_fails_build(pytester):
    # An UNREACHABLE store is inconclusive (?), which must never fail CI — even in strict.
    # (Renamed from test_strict_mode_fails_build_on_silent_loss, which tested *this*
    # invariant, the opposite of its old name; the real silent-loss-fails-build case is the
    # test below.)
    pytester.makepyfile("def test_ok():\n    assert True\n")
    pytester.makeini(
        "[pytest]\nooptdd_backend = openobserve\nooptdd_enabled = 1\nooptdd_verify = strict\n"
    )
    # no OOPTDD_OO_URL -> backend query unreachable -> inconclusive -> build NOT failed
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
    assert result.ret == 0  # inconclusive must never break CI, even in strict


# A reachable store that silently drops everything shipped: ship is a no-op but query
# round-trips and returns nothing -> a real `absent` (⊥), i.e. silent ingest loss.
_DROP_CONFTEST = (
    "from ooptdd.backends import default_registry, MemoryBackend\n"
    "default_registry.register('dropmem', lambda **o: MemoryBackend(drop=True))\n"
)


def test_strict_mode_fails_build_on_silent_loss(pytester):
    # The headline guarantee, now actually tested at the plugin level: strict + a real silent
    # loss (reachable store, nothing arrived) must FAIL the build. The dropping-but-reachable
    # backend yields ⊥ absent (not ? inconclusive), so strict turns it red.
    pytester.makeconftest(_DROP_CONFTEST)
    pytester.makepyfile("def test_ok():\n    assert True\n")
    pytester.makeini(
        "[pytest]\nooptdd_backend = dropmem\nooptdd_enabled = 1\nooptdd_verify = strict\n"
        "ooptdd_delay = 0\nooptdd_retries = 1\n"  # no real poll delay — absent is immediate
    )
    result = pytester.runpytest_subprocess()
    result.stdout.fnmatch_lines(["*[ooptdd]*silent ingest loss*"])
    assert result.ret != 0  # strict turns a real arrival miss into a build failure


def test_plugin_ships_and_confirms_under_xdist(pytester):
    # Regression for the xdist false-green: reports must be collected on the controller (via
    # pytest_runtest_logreport), so a `-n` run ships + verifies exactly like a serial run. It
    # used to do NOTHING under -n — makereport fired only on the workers, so the controller
    # collected zero reports and silently shipped/verified nothing.
    pytester.makepyfile(
        "def test_a():\n    assert True\n"
        "def test_b():\n    assert True\n"
        "def test_c():\n    assert True\n"
    )
    pytester.makeini("[pytest]\nooptdd_backend = memory\nooptdd_enabled = 1\n")
    result = pytester.runpytest_subprocess("-n", "2")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(["*[ooptdd]*arrival confirmed*"])
