# Releasing

A published version can't be changed or reused, so these run in order, and each must pass before the next. Publishing is always the owner's call: **ask before step 7.**

## 1. Prepare

- [ ] Raise `__version__` in `packages/try-professional/src/try_professional/__init__.py`.
- [ ] Raise `skill-customs` too, only if it changed. Check that `try-professional`'s dependency range still allows it.
- [ ] Update `CHANGELOG.md`, and the version in the README's first lines. Run `python scripts/package_readme.py`, which writes the README PyPI shows, with links that work there. A test fails if it's out of date.
- [ ] Check every number in the README still has something behind it.

## 2. Check locally

```bash
ruff check . && ruff format --check . && mypy
pytest -m "not model"
pytest -m model --timeout=600
python scripts/generate_schemas.py
python scripts/secret_scan.py
```

- [ ] All of it passes, and `generate_schemas.py` changes nothing.
- [ ] No personal data or local paths in anything new: `git grep -E "AppData|C:/Users|/home/"`.

## 3. Push, and run everything

- [ ] Push to `main`. The pre-push hook runs the secret scan again.
- [ ] Run the full CI by hand: `gh workflow run ci.yml --ref main`. It covers 3 systems and 5 Python versions. The publish workflow refuses a commit without a passing manual run.

## 4. Try it the way a user would

- [ ] Build the wheels, and install them into a new environment.
- [ ] Run `trypro doctor`, `trypro doctor --report`, `trypro init --skip-model`, and `trypro run --dry-run "Build a health check endpoint"`.
- [ ] If the pipeline, prompts, or Claude Code files changed, run the quickstart in a real terminal, through `/company`. This uses a Claude plan.
- [ ] If reviews or prompts changed, rerun `docs/research/qa_gates/run_seeded_defects.py`, and update what the README says.

## 5. Read the diff

- [ ] Read everything since the last release, as a user would meet it.

## 6. Update the docs for what was just learned

- [ ] Anything the steps above found goes in the changelog, known limits, or both.

## 7. Ask, and publish

- [ ] **The owner approves this exact commit.**
- [ ] If `skill-customs` changed, publish it first: `gh workflow run publish.yml --ref main -f package=skill-customs`.
- [ ] `gh workflow run publish.yml --ref main -f package=try-professional`.

Publishing uses Trusted Publishing, so there's no token. PyPI trusts `publish.yml` in the `pypi` environment, which only accepts `main`.

## 8. Confirm

- [ ] Install from PyPI into a new environment, with no cache, and repeat step 4's commands.
- [ ] Tag it, and write the release from the changelog: `gh release create v<version> --target <commit> --title v<version> --notes-file <notes>`.
- [ ] Set the changelog's date.

## If it goes wrong

A broken version can't be replaced, only followed. Yank it on PyPI, which stops new installs from choosing it, and publish the next version with the fix.
