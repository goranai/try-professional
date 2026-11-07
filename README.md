# Try Professional

**Preview (v0.1.0). It works, it has rough edges, and it has only been proven on Claude.**

Turn an agent into a permanent company, not a one-off project. Describe a business, approve the company it designs, and give it commands. It works only when you give it a command, and it remembers between commands: every role, from the CEO down, keeps its own memory and knows what it's working on.

Each piece of work has one owner, gets reviewed, and leaves an audit log you can verify. The rules that matter are enforced by code, not by asking a model nicely.

## Quickstart

No key, no cost. A scripted model stands in, so this shows how the company works, not what a model would write:

```bash
pip install try-professional
mkdir my-company && cd my-company
trypro init
trypro run --dry-run "Build a health check endpoint"
trypro check-model --dry-run
```

`trypro init` downloads a local embedding model, about 0.22 GB, once. Your text is embedded on your machine and isn't sent anywhere.

### With Claude Code

No API key is needed. It uses your own Claude plan, and each role runs as its own subagent:

```bash
pip install 'try-professional[mcp]'
mkdir my-company && cd my-company
claude mcp add trypro -- trypro mcp --coordinator --dir "$PWD"
claude "Use the trypro tools to design a company for a catering business that delivers office lunches."
```

It stops at a review screen, because **a person approves a company, in a terminal:**

```bash
trypro init --review <proposal-id>
trypro agents claude-code
```

Restart Claude Code in that folder, and run:

```text
/company Write a weekly lunch menu and a price list for a new office client with 30 people
```

### With any OpenAI-compatible model

Copy `.env.example` to `.env`, and set `TRYPRO_LLM_BASE_URL`, `TRYPRO_LLM_API_KEY`, and `TRYPRO_LLM_MODEL`. **Check the model first:**

```bash
trypro check-model
trypro init "a YouTube Shorts content company"
trypro run "Make 5 Shorts about AI tools for this week"
```

`check-model` tests tool calling, strict structures, following a brief, replying in the command's language, and treating data as data. Then it has the model finish one small real command, through every review, within 40,000 tokens. It reports **suitable**, **usable with caveats**, or **not recommended**, and it can spend up to about 45,000 tokens. It's one run, so a different run can go differently.

### Once a company exists

```bash
trypro run "<a command>"          # Or --queue to add it to the backlog
trypro next                       # Run the oldest queued command
trypro resume <run-id>            # Continue unfinished work
trypro cancel <run-id>            # Stop it. Its records stay
trypro status                     # Backlog, memory size, tokens by month and command
trypro audit <run-id>             # Verify a run's audit log
```

Results are files, in `deliverables/<run-id>/`. Start with `index.md`.

## What works

- **A company from one description**, adapted from five industry templates, in any language. It proposes, and a person approves.
- **Skills from GitHub, vetted first.** `skill-customs` pins each one to a commit and checks its license and content, and a model judges which ones fit each role. It can also be used [on its own](packages/skill-customs/README.md).
- **A permanent company.** Roles have their own memory, departments and the company have shared memory, and later commands get the finished work of earlier ones.
- **Reviews that can't be skipped.** QA reviews everything. Legal review is triggered by what the work is, whatever a model declares.
- **Rules held by code.** Work must report on every item of its brief. Reviews can't approve with blockers. Unmet criteria and unverified claims reach you even if a final report leaves them out. Every command stops at a token limit: 40,000 for a small one and 150,000 for a large one, by default. [Tests play a misbehaving model](packages/try-professional/tests/test_misbehaving_model.py) against these.
- **Real separation over MCP.** Each role's server shows it only its own assignment, memory, and skills, and the session's server can't do a role's work.

## What was tested, and how

| Model | How | Result |
|---|---|---|
| `claude-sonnet-5-5` for every role, and `claude-haiku-5-5` for matching skills | Claude Code 2.1.319 on Windows 11, through MCP, each role as its own process with only that role's server | 9 of 9 commands finished, on 3 businesses. All 108 artifacts were recorded as coming from the right role's own server. [Details](docs/research/journey/README.md) |
| `deepseek-v4.1-flash-1017` | API runner | `check-model`: usable with caveats. **The same small command finished once in three runs**, and the other two blocked. [Why](docs/research/journey/deepseek-diagnosis.md) |

Those are the only models tested. The test suite runs on a scripted model, on Linux, macOS, and Windows.

**This doesn't show that a cheap model does good work.** Quality on a cheap model is for v0.2.

## What it costs

Tokens per command on Claude, measured. It's run through Claude Code, so each role's process also carries Claude Code's own instructions, which is most of the cached input. An API model's numbers will differ. The price is what Claude Code reports at API list prices. On a Claude plan, this comes out of your plan's usage instead.

| Business | Command | Input | Cache write | Cache read | Output | List price |
|---|---|---|---|---|---|---|
| Catering | Weekly menu and price list | 36 | 26,552 | 88,837 | 6,360 | $0.18 |
| Catering | Two-sentence customer reply | 40 | 24,500 | 102,816 | 4,254 | $0.15 |
| Catering | Next week's menu, with no repeats | 28 | 22,852 | 78,370 | 5,774 | $0.16 |
| Shorts | 5 Shorts | 134 | 172,426 | 454,803 | 55,696 | $1.29 |
| Shorts | One title | 36 | 21,510 | 86,108 | 3,665 | $0.13 |
| Shorts | 3 more, building on last week | 140 | 251,760 | 573,295 | 60,220 | $1.67 |
| Indonesian snacks | Two-week promotion plan | 40 | 34,659 | 109,538 | 11,300 | $0.26 |
| Indonesian snacks | One customer reply | 36 | 22,775 | 86,890 | 4,230 | $0.14 |
| Indonesian snacks | The next two weeks | 44 | 39,627 | 138,566 | 13,481 | $0.31 |

A hierarchy spends more than one agent would. On the small commands, reviews alone took 39% to 46% of the output tokens. No comparison with a flat setup has been measured yet, and that benchmark is planned for v0.2.

On DeepSeek through the API runner, the one small command that finished took 25,020 tokens: [details](docs/research/journey/check_model_deepseek.json).

## What it needs

- **Python 3.10 or newer.**
- **Memory:** about 750 MB for a typical command with 22 roles, because the company shares one embedding process. The most measured in a real command was 829 MB. Having every role's server open at once takes about 2.7 GB. [Measured on Windows 11](docs/research/memory/README.md). It waits until 1.8 GB is free before starting, and that includes a 1 GB safety margin. Change it with `TRYPRO_MEMORY_MARGIN_MB`.

## Known limits

- **It's only been proven on Claude.** On a cheap model, expect blocked commands and wasted tokens. Run `trypro check-model`, and start small.
- **Skill coverage is thin.** In real generations, 14% to 29% of what roles need had a matching skill, and the review screen says so. Roles without a skill rely on their instructions. Judged on a labelled set, skill matching found 33 of 44 right skills, and 19 of its 52 suggestions were wrong, so read what you approve.
- **Cost per command is high** for small jobs. See above. A two-sentence reply goes through intake, the work, QA, legal, and a final report.
- **It works with files only.** It doesn't publish, send, buy, render video, or browse the web unless you turn on search. Its output tells you what a person must check.
- **It can't check facts without sources.** With no web access, it marks claims as unverified, and leaves placeholders such as `[Bakery name]` or `[ISI PROMO]` for you to fill in.
- **Reviews approved everything in the final journey.** Earlier passes did send work back, but these 9 commands don't show reviews catching a problem. A weak reviewer can still approve weak work.
- **Over MCP, token limits run on an estimate** of what passes through the server, which undercounts by several times. Your client's own limits matter more there.
- **Operator mode over MCP** has one model play every role, so separation there is only procedural. Use `trypro agents claude-code`, or one bound server per role.
- **Hermes isn't supported yet.** There's no Hermes adapter in this version, so it's untested. `skill-customs` has a guide for it that hasn't been tried on a real install.
- **Indonesian output hasn't been reviewed by a native speaker.** Other languages haven't been tried on full commands. Retrieval queries in other languages haven't been natively reviewed either.
- **The journey ran on one Windows machine,** and on Claude Code 2.1.319. Linux and macOS have only run the test suite.

## Coming in v0.2

Self-refinement toward quality on cheap models, a Hermes adapter, a three-way benchmark against a flat swarm and a single agent, and dreaming, where a company consolidates what it learned.

## Learn more

- [Architecture](docs/architecture.md)
- [The MCP adapter, and what it can and can't guarantee](packages/try-professional/src/try_professional/adapters/mcp/README.md)
- [Flagship example: a YouTube Shorts studio](examples/youtube-shorts-studio/README.md)
- [Contributing](CONTRIBUTING.md), and [security](SECURITY.md)

MIT licensed. No telemetry. Keys come from environment variables, and are never logged.
