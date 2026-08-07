# ooptdd-genai

Optional GenAI event builders, semantic-convention ontologies, OpenLLMetry translation,
and evaluation-platform bridges for `ooptdd`.

```bash
python -m pip install 'ooptdd>=0.6,<0.7' 'ooptdd-genai>=0.1,<0.2'
# Add the optional bridge only when needed:
python -m pip install 'ooptdd-genai[deepeval]>=0.1,<0.2'
```

Import the API you need explicitly. Importing the package performs no registration:

```python
from ooptdd_genai import execute_tool_event, gen_ai_ontology

event = execute_tool_event(trace_id="trace-1", tool_name="search")
ontology = gen_ai_ontology()
```

`ontology_presets()` returns the available ontology factories without registering them.
This distribution intentionally installs no CLI and no predicate provider.
