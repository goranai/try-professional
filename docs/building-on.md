# Build on Try Professional

How to use a company as a part of your own agent, with Claude Code as the best-supported base. What's **stable** here keeps working across 0.x minor versions; a change that breaks it is announced in the CHANGELOG one minor version ahead. What's **experimental** can change in any release.

## What's stable, and what isn't

| Part | Status | Where |
|---|---|---|
| `Company`: create, open, add roles, validate, set up Claude Code, run, read results | **Stable** | `try_professional.api`, exported as `try_professional.Company` |
| The company files: `org/org.yaml` and `org/roles/<department>/<role>.yaml` | **Stable** | [docs/spec/org-v1.md](spec/org-v1.md) |
| The Claude Code files: `/company`, one subagent per role, `.mcp.json` | **Stable** to use; their contents can change | written by `Company.setup_claude_code()` or `trypro agents claude-code` |
| The CLI commands in the README's quickstart | **Stable** | `trypro --help` |
| The MCP tools (`run_start`, `run_next_action`, the submit tools) | Experimental | `adapters/mcp` |
| The adapter interface for other runtimes | Experimental | `adapters/base.py`, `adapters/template/` |
| The pipeline, orchestrator, memory, and skill matching internals | Internal | anything not listed here |

**Two things stay with a person, by design, and no API does them:** approving a company the AI designed (`trypro init --review`), and answering a run's questions (`trypro decide`, at a terminal). An agent can show a person the question, and can't answer it for them.

## Ways to talk to a company

| Front door | Engine | Command |
|---|---|---|
| Claude Code | Claude Code, on a Claude plan | open the folder, and say what you want |
| Terminal | its own engine, any OpenAI-compatible model | `trypro chat` |
| Telegram | its own engine | `trypro telegram`, with `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_CHAT_IDS` in `.env` |

In both engines, roles that do or check work have a computer. On Claude Code, that's Claude Code's own tools. On its own engine, it's `shell`, in the run's workspace, and `web_fetch`. A command that changes something outside, such as a push or a new issue, needs a person's approval. With no person to ask, it doesn't run. `computer_tools: false` in `trypro.yaml`, or `trypro agents claude-code --sandboxed`, keeps a company to documents.

`try_professional.chat.Conversation` is the core every front door shares: `Conversation(root, ask_person=..., approve=...).handle(message)`. It's experimental, and a good start for your own front door.

## The quickest start

`trypro start "<what you want>"` in an empty folder does the steps below in one go: setup, a design, a person's approval, and the first job, in Claude Code. Afterwards, plain requests in Claude Code go to the company, through the front desk in `CLAUDE.md`.

## The Claude Code path, in four steps

1. **Install:** `pip install "try-professional[mcp]"`.
2. **Make a company.** Either the default one, from ready-made departments, with no model and no cost:
   ```python
   from try_professional import Company

   company = Company.create("acme", name="Acme")
   ```
   or one designed for a business, which a person approves, as in the [README's quickstart](../README.md#quickstart): `trypro setup claude-code`, then `claude "Use the trypro tools to design a company for <your business>."`, then `trypro init --review <proposal-id>` at a terminal.
3. **Write Claude Code's files:** `company.setup_claude_code()`, or `trypro agents claude-code`. Run it again whenever roles change.
4. **Run a command:** open the folder in Claude Code, and `/company <goal>`. Each role is its own subagent, with only its own role's server. When a run stops for a person, `trypro decide` answers it, and `/company continue <run-id>` goes on.

From your own agent's code, `examples/claude-code-agent/delegate.py` does step 4 headlessly, with `claude -p`, and reads back what the company made.

## Custom roles

```python
company.add_role(
    "marketing",
    "newsletter-editor",
    title="Newsletter Editor",
    mission="Write the weekly customer newsletter.",
    responsibilities=["Write in the customer's language", "Cite a source for each claim"],
    skill_needs=["write email newsletters"],  # what skill matching looks for
    skill_categories=["content"],
)
company.setup_claude_code()  # gives the new role its subagent
```

The role joins an existing department, reports to its head, and its head reviews its work. Any field of [the role spec](spec/org-v1.md) can be set with a keyword, such as `model_tier="fast"` or `reviewer="legal/counsel"`. If the company wouldn't validate, nothing is written, and `CompanyError` says why. Editing `org/roles/*.yaml` by hand works too; check it with `company.validate()` or `trypro org validate`.

A new department is a pack: `trypro pack list`, and `trypro pack add <name>`. Writing your own is an edit to `org/org.yaml`, as the spec describes.

## Running from Python

```python
result = company.run("Write a launch note", dry_run=True)  # a scripted model: free, and not real work
result = company.run("Write a launch note")  # the model trypro.yaml names
company.status(result.run_id)  # phase, open questions, tokens
company.deliverables(result.run_id)  # the folder a person reads: approved work, or labelled drafts
company.final_report(result.run_id)  # the final report's content, once done
company.runs()  # every run, newest first
```

`run` uses the API runner, with a model from `trypro.yaml` or the `client` you pass. On Claude Code, use `/company` instead, as above: it's the path most tested, and it runs on a Claude plan.

## Extending it

- **Skills:** `skill-customs` installs, scans, and pins third-party skills. `trypro skills --help`.
- **Playbooks:** each level of role, CEO, manager, and staff, works from a playbook in `_playbooks/`. They're internal for now.
- **Another runtime:** copy `adapters/template/`, and implement the interface in `adapters/base.py`. It's experimental, and [an issue tracks Hermes](https://github.com/goranai/try-professional/issues?q=is%3Aissue+label%3Aadapter).

## An example

`examples/claude-code-agent/`:
- `build_company.py` makes a company with one custom role, and writes Claude Code's files. It's tested in CI.
- `delegate.py` is a tool another agent can call: it hands the company a goal through `claude -p "/company …"`, and returns the run's status, any questions for a person, where the work is, and the final report.
