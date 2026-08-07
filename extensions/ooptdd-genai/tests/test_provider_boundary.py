from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import ooptdd_genai


def test_ontology_provider_is_explicit_and_immutable():
    presets = ooptdd_genai.ontology_presets()
    assert set(presets) == {"gen_ai@1.30", "gen_ai@1.41"}
    assert presets["gen_ai@1.30"]().get("gen_ai.execute_tool") is not None


def test_package_import_does_not_load_optional_modules():
    source = Path(__file__).resolve().parents[1] / "src"
    code = f"""
import sys
sys.path.insert(0, {str(source)!r})
import ooptdd_genai
loaded = set(sys.modules)
assert 'ooptdd_genai.events' not in loaded
assert 'ooptdd_genai.openllmetry' not in loaded
assert 'ooptdd_genai.semconv' not in loaded
assert 'ooptdd_genai.integrations' not in loaded
"""
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
