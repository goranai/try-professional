# MCP adapter

Pull mode: any MCP client can drive the company. The client's own model does the work, so Try Professional needs no model key here.

```bash
pip install 'try-professional[mcp]'
trypro init
trypro mcp                    # operator mode
trypro mcp --role engineering/backend   # bound to one role
```

Add it to your client's MCP servers. For example:

```json
{
  "mcpServers": {
    "try-professional": {
      "command": "trypro",
      "args": ["mcp"],
      "cwd": "/path/to/your/project"
    }
  }
}
```

## The loop

1. `run_start` with a goal.
2. `run_next_action` returns what to do, each with its role's instructions, the assignment, and the output schema.
3. Do each action as the role it names, and submit it with the tool it names.
4. A rejection says what to fix, and the same action stays open. Repeat until the status is `done`.

If a run needs a person, the status is `blocked`, and the response gives the question and a `trypro decide` command. A client can't answer it.

## What this mode can and can't guarantee

| | Bound (`--role`) | Operator |
|---|---|---|
| Order of steps, schemas, and review gates | Enforced | Enforced |
| Identity | Fixed when the server starts, and recorded as attested | Recorded as `claimed` |
| Separation between roles | One server per role | **Procedural only**: one model plays every role |

**A reviewer in operator mode isn't independent.** It's the same model that did the work. The audit log says so, by recording `attested_by: claimed`.

**Tool limits only cover this server's tools.** A role's `allowed_tools` apply to memory and skill tools here. Your client's own tools, such as a shell or file editor, are outside this server's control. Use the API runner if you need those enforced.

**Token ceilings don't apply.** The server can't see what your client spends. `usage_report` lets a client report its tokens, and they're marked as self-reported.

## Not available here

Approving a skill, admitting one to a loadout, categorizing one, and answering a run's questions. These are for people, at a terminal.

## Smoke test

No key or model is needed: `pytest packages/try-professional/tests/test_mcp.py`
