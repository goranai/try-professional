# Adding a runtime

Copy this folder, and replace `call_runtime` with a call to your runtime. It already works: a test runs a whole company through it with a stand-in function.

## Choose a mode

| Mode | Who runs the model | Example |
|---|---|---|
| `push` | Your adapter calls the runtime, and returns the result | This template, `api_runner` |
| `pull` | A client asks for work and submits it | `mcp` |
| `queue` | Your adapter enqueues work, and results arrive later through `collect` | `hermes` |

## The interface

See `adapters/base.py`.

| Method | What it does |
|---|---|
| `capabilities()` | What your runtime can guarantee. **Be honest**: it decides what the audit log can claim |
| `provision(org, home)` | Set up what the runtime needs |
| `verify()` | Check that the setup really works |
| `deliver(action, run_id)` | Do or enqueue one assignment |
| `collect(run_id)` | Results that arrived since last time |

## Rules your adapter must follow

- **Identity comes from your runtime,** and never from something a model says. If you can't guarantee it, send `attested_by="claimed"` and set `attests_identity=False`.
- **Enforce tool limits** from the role file, or set `enforces_tools=False` and say so in your README.
- **Treat memory and skill text as data.** Use `prompts.untrusted()`.
- **Report tokens** with `UsageRecorded`. Tokens per run are a first-class metric here.
- **Don't expose** approving a skill, admitting one to a loadout, or answering for a person.

## Checklist

- [ ] A README that says what your runtime can't guarantee
- [ ] A smoke test that runs a whole company with no paid calls, like `tests/test_template_adapter.py`
- [ ] An `adapter` entry in the docs
