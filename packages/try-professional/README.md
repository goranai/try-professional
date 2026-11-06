# Try Professional

Turn an LLM agent runtime into a structured company: one accountable owner per task, typed handoffs, review gates, multilingual memory, and a full audit log.

**Status: pre-release, Phase 3 of 7.** The org spec, pipeline, audit log, API runner, and multilingual memory exist. The skill supply chain, the MCP and Hermes adapters, and benchmarks don't exist yet.

```bash
pip install -e packages/try-professional
trypro init
trypro run --dry-run "Build a health check endpoint"
```

`--dry-run` uses a scripted model, so it needs no API key and spends nothing. `trypro init` downloads the local embedding model, about 0.22 GB.

To use a real model, copy `.env.example` to `.env` and set `TRYPRO_LLM_BASE_URL`, `TRYPRO_LLM_API_KEY` and `TRYPRO_LLM_MODEL`. Any OpenAI-compatible API works. Runs stop at a token ceiling, set in `org/org.yaml`, and ask before continuing.

See [docs/architecture.md](https://github.com/goranai/try-professional/blob/main/docs/architecture.md).
