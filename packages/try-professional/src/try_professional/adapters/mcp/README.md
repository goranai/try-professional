# MCP adapter

Pull mode: an MCP client drives the company, and its own model does the work, so Try Professional needs no model key here. **Claude Code is the client this is built and tested for. Others are experimental: available, not promised.**

```bash
pip install 'try-professional[mcp]'
```

## In Claude Code

```bash
mkdir my-company && cd my-company
trypro setup claude-code
```

That checks Python, Claude Code, and `GITHUB_TOKEN`, and adds the coordinator's server to Claude Code for this folder. Without a token a company gets no skills, and it says how to set one. Then ask Claude Code to design your company:

```bash
claude "Use the trypro tools to design a company for a catering business that delivers office lunches."
```

To add the server by hand instead: `claude mcp add trypro -- trypro mcp --coordinator --dir "$PWD"`.

It calls `org_propose`, does the design and skill matching with its own model, and stops at a review screen. **A person approves, in a terminal:**

```bash
trypro init --review <proposal-id>
```

Approving writes one subagent per role and a `/company` command, in a folder `trypro setup claude-code` set up. Elsewhere, run `trypro agents claude-code` after approving. Restart Claude Code in this folder, so it loads them. Say yes when it asks whether you trust the folder, or each role's server can't start. Then run:

```text
/company Write a weekly lunch menu and a price list for a new office client with 30 people
```

Your session only coordinates. Its server can start work and see who's next, and it can't read an assignment or submit anything, so it can't do a role's work. Each role's subagent has no shell and no file tools. Its only tools come from a server started for that role, so it can't read another role's memory or see the rest of the company, whatever it tries. Claude Code's own limits apply to its usage: Try Professional can't see them.

If a command stops, for example at its token limit, it shows you a `trypro decide` command. Run that, and then `/company continue <run-id>`.

## In any other client (experimental)

This hasn't been tried on Cursor, Codex, or any client but Claude Code.

```bash
trypro mcp                              # Operator mode: one client plays every role
trypro mcp --role engineering/backend   # Bound to one role
trypro mcp --coordinator                # Starts and follows work, and can't do any of it
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
4. A rejection says what to fix, and the same action stays open. Repeat until the status is `done`, and the response gives the path of the deliverables.

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

**A coordinator** starts and follows work, and sees who's next. It can't read an assignment, submit anything, or reach memory, skills, or workspace files. Pair it with one bound server per role, which is what `trypro agents claude-code` sets up.

**A reviewer in operator mode isn't independent.** It's the same model that did the work. The audit log says so, by recording `attested_by: claimed`. Use bound servers, as `trypro agents claude-code` does, if that matters. Check it afterwards: `attested_by: runtime` in the audit log is what shows a role's own server did the work.

**Tool limits only cover this server's tools.** A role's `allowed_tools` apply to memory, skill, and workspace tools here. A client's own tools, such as a shell or file editor, are outside this server's control. `trypro agents claude-code` turns them off for each role. Another client has to do the same, or use the API runner.

**Token limits run on an estimate.** The server can't see what a client spends, and can't trust what it says, so it counts what passes through it, at about 3.5 characters per token, and multiplies that by 3.3. Across 17 commands measured through Claude Code, a client's fresh tokens were 3.3 times this count, from 2.3 to 4.6 ([the runs](../../../../../../docs/research/mcp_estimate/README.md)). Limits over MCP are 150,000 for a small command and 500,000 for a large one, by default, in those corrected tokens. `usage_report` records a client's own number beside it, marked as self-reported, and limits don't use that.

## Not available here

Approving a company, approving a skill, admitting one to a loadout, categorizing one, changing company memory, and answering a run's questions. These are for people, at a terminal.

## Smoke test

No key or model is needed: `pytest packages/try-professional/tests/test_mcp.py packages/try-professional/tests/test_mcp_design.py`
