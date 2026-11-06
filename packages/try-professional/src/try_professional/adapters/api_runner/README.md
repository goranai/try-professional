# API runner adapter

Push mode: this adapter calls the model itself, and drives a run from goal to final deliverable. Identity is attested and tool permissions are enforced by the runner.

## Configure

`trypro init` writes `trypro.yaml`, which names environment variables and never holds keys. Values come from the environment, then from the nearest `.env`.

```dotenv
TRYPRO_LLM_BASE_URL=https://api.example.com/v1
TRYPRO_LLM_API_KEY=your-key-here
TRYPRO_LLM_MODEL=your-model-name
```

Any OpenAI-compatible API works. If an API wants `max_completion_tokens`, set `model.max_tokens_param` in `trypro.yaml`.

**One agent, its own key or model:** add this to `org/org.yaml`. A role's fields win over its department's.

```yaml
llm_overrides:
  engineering: { model: another-model }
  engineering/qa: { api_key_env: QA_LLM_KEY }
```

**Claude** uses the official SDK: `pip install 'try-professional[anthropic]'`, then see the commented block in `trypro.yaml`. Its `fallbacks: default` setting lets the API retry a declined request on another model, and the audit log records which model answered.

## Smoke test

Uses a scripted model, so it needs no key and costs nothing:

```bash
trypro run --dry-run "Build a health check endpoint"
trypro doctor
```

Automated version: `packages/try-professional/tests/test_runner.py`.

## Limits

- There is no shell tool. Roles read and write files in the run's workspace, and can't leave it.
- A single model call can overshoot `max_tokens_per_run`. The run blocks as soon as the usage is recorded, before the next call.
