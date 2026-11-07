**What changed, and why:**

**The test that would have failed before:**

- [ ] `ruff check . && ruff format --check . && mypy` and `pytest -m "not model"` pass
- [ ] Packaged copies are updated, if a schema, template, or playbook changed (`python scripts/generate_schemas.py`)
- [ ] Nothing weakens QA, legal review, skill vetting, secret handling, or budget limits
