# Try Professional

Turn an agent into a permanent company, not a one-off project. It works only when you give it a command, and it persists between commands: every agent, from the CEO down, keeps its own memory and knows what it's working on.

**Status: pre-release. Most of that isn't built yet.** Today's version gives you one accountable owner per task, typed handoffs, review gates, multilingual memory shared at company and department level, a vetted skill supply chain, and a full audit log. Generating a company from a description exists, but hasn't been proven on a real model. Personal memory, work state across commands, wake-up briefs, management playbooks, Hermes support, and benchmarks don't exist yet.

```bash
pip install -e packages/try-professional
trypro init
trypro run --dry-run "Build a health check endpoint"
```

`--dry-run` uses a scripted model, so it needs no API key and spends nothing. `trypro init` downloads the local embedding model, about 0.22 GB.

Once a company exists:

```bash
trypro run "<a command>"          # Or --queue to add it to the backlog without running it
trypro next                       # Run the oldest queued command
trypro resume <run-id>            # Continue unfinished work
trypro cancel <run-id>            # Stop it. Its records stay
trypro status                     # Backlog, handoffs, memory size per tier, tokens by month and command
trypro status --role <dept/role>  # One role's card and work state
trypro memory review --by <you>   # Decide what the CEO proposed for company memory
trypro compact                    # Summarize old logs, and archive raw ones. Uses no model
```

To use a real model, copy `.env.example` to `.env` and set `TRYPRO_LLM_BASE_URL`, `TRYPRO_LLM_API_KEY` and `TRYPRO_LLM_MODEL`. Any OpenAI-compatible API works. Runs stop at a token ceiling, set in `org/org.yaml`, and ask before continuing.

See [docs/architecture.md](https://github.com/goranai/try-professional/blob/main/docs/architecture.md).
