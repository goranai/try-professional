# A company your agent can delegate to

```bash
pip install "try-professional[mcp]"
python build_company.py acme          # the default company, plus a Newsletter Editor. No model, no cost
cd acme && claude                     # then: /company Write this week's newsletter
```

Or, from your own agent, as a tool:

```python
from delegate import delegate

outcome = delegate(Path("acme"), "Write this week's newsletter")
# {"run_id": ..., "status": "done", "questions_for_a_person": [], "deliverables": ..., "report": {...}}
```

`delegate` runs `/company` headlessly, on your Claude plan, and reads the result through the stable API. If the run stops for a person, `questions_for_a_person` says what it needs. A person answers with `trypro decide` in that folder, because no agent can answer for them. More in [docs/building-on.md](../../docs/building-on.md).
