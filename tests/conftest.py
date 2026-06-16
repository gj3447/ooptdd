import pytest

from ooptdd.backends import memory_reset

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _clean_memory_store():
    memory_reset()
    yield
    memory_reset()
