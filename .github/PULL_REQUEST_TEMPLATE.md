**What changed, and why:**

**Which issue this closes:** #

**The test that would have failed before:**

**How you checked it:** the commands you ran, and if this touches a runtime or a real model, which one. Never paste a key.

- [ ] `pytest -m "not model"` passes, and so do `ruff check .`, `ruff format --check .`, and `mypy`
- [ ] Packaged copies are updated, if a schema, template, or playbook changed (`python scripts/generate_schemas.py`)
- [ ] `CHANGELOG.md` has a line, if a user would notice this
- [ ] Nothing weakens QA, legal review, skill vetting, secret handling, or budget limits
