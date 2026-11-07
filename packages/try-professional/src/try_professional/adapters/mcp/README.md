# MCP adapter

Pull mode: any MCP client can drive the company. The client's own model does the work, so Try Professional needs no model key here.

```bash
pip install 'try-professional[mcp]'
```

## In Claude Code

```bash
mkdir my-company && cd my-company
trypro mcp --help                 # Check it's installed
```

Add the server to Claude Code, and ask it to design your company:

```bash
claude mcp add trypro -- trypro mcp --dir "$PWD"
claude "Use the trypro tools to design a company for a catering business that delivers office lunches."
```

It calls `org_propose`, does the design and skill matching with its own model, and stops at a review screen. **A person approves, in a terminal:**

```bash
trypro init --review <proposal-id>
trypro agents claude-code
```

That writes one subagent per role and a `/company` command. Restart Claude Code in this folder, so it loads them, and run:

```text
/company Write a weekly lunch menu and a price list for a new office client with 30 people
```

Each role's subagent has no shell and no file tools. Its only tools come from a server started for that role, so it can't read another role's memory or see the rest of the company, whatever it tries. Claude Code's own limits apply to its usage: Try Professional can't see them.

## In any other client

```bash
trypro mcp                              # Operator mode
trypro mcp --role engineering/backend   # Bound to one role
```

```json
{
  "mcpServers": {
    "try-professional": {
      "command": "trypro",
      "args": ["mcp", "--dir", "/path/to/your/project"]
    }
  }
}
```

1. `run_start` with a goal.
2. `run_next_action` returns what to do, each with its role's instructions, the assignment, and the output schema.
3. Do each action as the role it names, and submit it with the tool it names.
4. A rejection says what to fix, and the same action stays open. Repeat until the status is `done`.

If a run needs a person, the status is `blocked`, and the response gives the question and a `trypro decide` command. A client can't answer it.

Designing a company is the same loop: `org_propose`, then `org_proposal_get` says what's needed next, and `org_proposal_submit` and `org_skill_judgment_submit` hand it in.

## What this mode can and can't guarantee

| | Bound (`--role`) | Operator |
|---|---|---|
| Order of steps, schemas, and review gates | Enforced | Enforced |
| Definitions of done, honest reviews, limits on plans and assignments | Enforced | Enforced |
| What a person is told: unmet criteria, failed tasks, unverified claims | Enforced | Enforced |
| Identity | Fixed when the server starts, and recorded as attested | Recorded as `claimed` |
| What a role can see | Its own assignment, memory, skills, and tasks | Everything |
| Separation between roles | One server per role | **Procedural only**: one model plays every role |
| Files a role claims to have written | Checked against the run's workspace | Not checked, because the client has its own files |

**A reviewer in operator mode isn't independent.** It's the same model that did the work. The audit log says so, by recording `attested_by: claimed`. Use bound servers, as `trypro agents claude-code` does, if that matters.

**Tool limits only cover this server's tools.** A role's `allowed_tools` apply to memory, skill, and workspace tools here. A client's own tools, such as a shell or file editor, are outside this server's control. `trypro agents claude-code` turns them off for each role. Another client has to do the same, or use the API runner.

**Token limits run on an estimate.** The server can't see what a client spends, and can't trust what it says, so budgets count what passes through the server, at about 3.5 characters per token. This misses the client's own system prompt and thinking, so it undercounts. `usage_report` records a client's own number beside it, marked as self-reported, and limits don't use that.

## Not available here

Approving a company, approving a skill, admitting one to a loadout, categorizing one, changing company memory, and answering a run's questions. These are for people, at a terminal.

## Smoke test

No key or model is needed: `pytest packages/try-professional/tests/test_mcp.py packages/try-professional/tests/test_mcp_design.py`
