# Contributing

Thanks for helping. The issues labelled `good first issue` are scoped so you can finish one without learning the whole project: templates, department packs, scanner rule packs, and language reviews need no knowledge of the pipeline.

## Your first pull request

1. **Pick something small.** The [good first issues](https://github.com/goranai/try-professional/issues?q=is%3Aissue+is%3Aopen+label%3A%22good%20first%20issue%22) are scoped for this. A template, a language review, or a runtime you already use are good places to start.
2. **Say you're on it** with a comment on the issue, so nobody duplicates it. If you have a question, ask there.
3. **Set up,** as below. It takes a few minutes, and no key.
4. **Make the change, with a test** that would have failed before it. A docs change or a language review doesn't need one.
5. **Commit.** The hook runs lint, types, and the fast tests, in about a minute, and blocks the commit if they fail.
6. **Open the pull request,** and fill in the template. Small ones get reviewed fastest.

Stuck? Open a draft pull request and ask, or use [Discussions](https://github.com/goranai/try-professional/discussions/categories/q-a). Nothing you can do in a fork can break anything here.

## Set up

```bash
git clone https://github.com/goranai/try-professional
cd try-professional
python -m venv .venv && . .venv/bin/activate    # On Windows: .venv\Scripts\activate
pip install -e packages/skill-customs -e "packages/try-professional[anthropic,mcp]" pytest pytest-timeout pytest-xdist ruff mypy types-PyYAML types-jsonschema
python scripts/install_hooks.py
```

## Check your work

```bash
ruff check . && ruff format --check . && mypy
pytest -m "not model"
```

These spend nothing. `install_hooks.py` sets up two hooks: before each commit, lint, types, and the fast tests run, in about a minute. Before each push, a secret scan runs. Run the whole suite yourself before a pull request.

CI runs Linux only on a branch or a pull request, and adds Windows and macOS on `main`. A `v*` tag or a manual run covers every system and Python version. Changes to docs alone don't run CI.

If you change a schema, a template, or a playbook, run `python scripts/generate_schemas.py`, so the packaged copies match.

## The rules this project keeps

- **Code enforces what matters.** Hierarchy, gates, memory permissions, work state, budgets, and skill vetting must hold whichever model runs. A prompt can ask for good behaviour, and only code can hold it. If you add a rule, add a case to `test_misbehaving_model.py`.
- **Nothing general depends on one business.** Industry specifics belong in templates and packs.
- **Every claim must be verifiable.** No invented benchmarks, and no number in the docs that a reader can't reproduce.
- **No telemetry, and few dependencies.** Keys come from environment variables and are never logged.
- **Tests never spend money.** A test that needs a real model isn't part of `pytest -m "not model"`.
- **Never improve a score by weakening QA, legal review, skill vetting, secret handling, or budget limits.**

## Pull requests

Keep them small, say what changed and why, and include the test that would have failed before. All code, docs, and commit messages are in English. Companies themselves work in any language.

By contributing, you agree that your work is released under the MIT license.
