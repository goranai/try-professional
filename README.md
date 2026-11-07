# Try Professional

**Turn Claude Code into a permanent AI company: one prompt builds a hierarchy of agents with roles, memory and vetted skills. Multi-agent orchestration via MCP.**

*Preview (v0.1.1). It works, it has rough edges, and it has only been proven on Claude.*

You describe a business, and Claude Code designs a company for it: departments, roles, and skills from GitHub that are checked before they're used. You approve it, and then you give it commands. It works only when you give it a command, and it remembers between commands: every role, from the CEO down, keeps its own memory and knows what it's working on.

## Multi-agent orchestration for Claude Code

Each piece of work has one owner, gets reviewed, and leaves an audit log you can verify. Each role runs as its own subagent, which sees only its own assignment, memory, and skills. The rules that matter are enforced by code, not by asking a model nicely.

- **A company from one description**, adapted from five industry templates, in any language. It proposes, and a person approves.
- **Skills from GitHub, vetted first.** `skill-customs` pins each one to a commit and checks its license and content, and a model judges which ones fit each role. It can also be used [on its own](packages/skill-customs/README.md).
- **A permanent company.** Roles have their own memory, departments and the company have shared memory, and later commands get the finished work of earlier ones.
- **Reviews that can't be skipped.** QA reviews everything. Legal review is triggered by what the work is, whatever a model declares.
- **Rules held by code.** Work must report on every item of its brief. Reviews can't approve with blockers. Unmet criteria and unverified claims reach you even if a final report leaves them out. Every command stops at a token limit: 60,000 for a small one and 200,000 for a large one, by default. [Tests play a misbehaving model](packages/try-professional/tests/test_misbehaving_model.py) against these.
- **Real separation over MCP.** Each role's server shows it only its own assignment, memory, and skills, and the session's server can't do a role's work.

## How it works

```text
/company <your command>
    |
    v
CEO: turns it into criteria someone can check
    |
    v
Department: does the work  <-------- sent back, saying what's wrong
    |                                    ^
    v                                    |
QA reviews it, and then Legal, if it's public or touches licenses or personal data
    |
    v
CEO: the final report, in your language
    |
    v
deliverables/<run-id>/index.md, and an audit log
```

1. **Design.** Claude Code calls the `trypro` tools to adapt one of five industry templates to your business, and to match skills to roles. [`skill-customs`](packages/skill-customs/README.md) pins each skill to a commit, and checks its license and content.
2. **Approve.** You read the proposal and approve it in a terminal. No model can do this.
3. **Roles.** `trypro agents claude-code` writes one subagent per role. Each has no shell and no file tools, only a server bound to that role.
4. **Commands.** Your session only coordinates. It can see who's next, and it can't read an assignment or submit anything. A small command goes straight to one role, and a large one is planned by department heads.
5. **Memory.** Roles keep their own memory, and later commands get the finished work of earlier ones.

## Quickstart

For Claude Code. You need Python 3.10 or newer, and Claude Code signed in. It uses your own Claude plan, with no API key, and each role runs as its own subagent.

**1. Install it, and make a folder for your company.**

```bash
pip install "try-professional[mcp]"
mkdir my-company && cd my-company
```

**2. Set a GitHub token, so it can search for skills.** Without one a company gets no skills, and the review screen says so. If you use the GitHub CLI:

```bash
export GITHUB_TOKEN=$(gh auth token)
```

**3. Add it to Claude Code.**

```bash
claude mcp add trypro -- trypro mcp --coordinator --dir "$PWD"
```

**4. Describe your business.** Claude Code designs a company and chooses skills, and then gives you a proposal id.

```bash
claude "Use the trypro tools to design a company for a catering business that delivers office lunches."
```

**5. Read the proposal, and approve it.** A person has to do this, in a terminal. No model can. The first approval downloads a local embedding model, about 0.22 GB. Your text is embedded on your machine and isn't sent anywhere.

```bash
trypro init --review <proposal-id>
```

**6. Create the company's roles.** This writes one subagent per role, and the `/company` command.

```bash
trypro agents claude-code
```

**7. Start Claude Code again in this folder, and give the company a command.** When it asks whether you trust this folder, say yes. Otherwise each role's server can't start, and `/company` stops rather than do a role's work itself.

```text
/company Write a weekly lunch menu and a price list for a new office client with 30 people
```

Your session only coordinates. Results are files, in `deliverables/<run-id>/`. Start with `index.md`.

### Once a company exists

```text
/company <a command>                          Give it more work. It remembers earlier work
trypro doctor                                 Check your setup, and how to fix what's wrong
trypro status                                 Memory size, and tokens by month and command
trypro audit <run-id>                         Verify a run's audit log
trypro decide <run-id> <request-id> resume    Answer a command that stopped to ask you
/company continue <run-id>                    Then carry on with it
trypro cancel <run-id>                        Stop it. Its records stay
```

## What was tested, and how

| Model | How | Result |
|---|---|---|
| `claude-sonnet-5-5` for every role, and `claude-haiku-5-5` for matching skills | Claude Code 2.1.319 on Windows 11, through MCP, each role as its own process with only that role's server | 9 of 9 commands finished, on 3 businesses. All 108 artifacts were recorded as coming from the right role's own server. [Details](docs/research/journey/README.md) |
| `claude-sonnet-5-5` for every role | `/company` in one Claude Code session, each role as a subagent, on a business kept out of all training | One prompt to a company, a command, and a later related command that followed the first. All 23 artifacts came from the right role's own server. It passed on its sixth run, and the fifth stopped at the token limit. [Every run](docs/research/journey/README.md#acceptance-journey) |
| `deepseek-v4.1-flash-1017` | API runner | `check-model`: usable with caveats. **The same small command finished once in three runs**, and the other two blocked. [Why](docs/research/journey/deepseek-diagnosis.md) |

Those are the only models tested. The test suite runs on a scripted model, on Linux, macOS, and Windows.

**This doesn't show that a cheap model does good work.** Quality on a cheap model is for v0.2.

## Do reviews catch flawed work?

We put flawed documents into real runs over MCP, and counted how many reviewers rejected for that flaw. [How it ran, and every verdict](docs/research/qa_gates/README.md).

| Flaw | Caught, on the current review rules |
|---|---|
| A wrong number | 6 of 6 |
| A claim with no source | 6 of 6 |
| The wrong language | 6 of 6 |
| A requirement missed from the brief | 6 of 6 |
| A clean document wrongly sent back | 1 of 6 |

**This is a small test, and it only shows so much.** One run of each document, on `claude-sonnet-5-5` only. The documents are short, with one flaw each, and every fact could be checked against the command. Long documents, wrong facts about the world, Legal on its own, and cheap models weren't tested.

The rules are stricter because of this test. On the earlier rules, a reviewer noted an invented claim as minor, and 3 of 4 were caught. The stricter rules also cost something: reviewers now send back some work that didn't need it.

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

Those ran before reviews got stricter, which adds rework. The acceptance run on the current rules shows that, with a `/company` session and its subagents together:

| Business | Command | Input | Cache write | Cache read | Output | List price |
|---|---|---|---|---|---|---|
| Web design agency | Design the company, from one prompt | 36 | 46,635 | 471,088 | 6,075 | $0.29 |
| Web design agency | One-page proposal, 1 round of rework | 90 | 77,480 | 743,298 | 15,794 | $0.46 |
| Web design agency | Homepage copy, 2 rounds of rework | 130 | 106,645 | 1,063,112 | 25,436 | $0.66 |

A hierarchy spends more than one agent would. On the first table's small commands, reviews alone took 39% to 46% of the output tokens. No comparison with a flat setup has been measured yet, and that benchmark is planned for v0.2.

On DeepSeek through the API runner, the one small command that finished took 25,020 tokens: [details](docs/research/journey/check_model_deepseek.json).

## What it needs

- **Python 3.10 or newer.**
- **Memory:** about 750 MB for a typical command with 22 roles, because the company shares one embedding process. The most measured in a real command was 829 MB. Having every role's server open at once takes about 2.7 GB. [Measured on Windows 11](docs/research/memory/README.md). It waits until 1.8 GB is free before starting, and that includes a 1 GB safety margin. Change it with `TRYPRO_MEMORY_MARGIN_MB`.

## Known limits

- **It's only been proven on Claude.** On a cheap model, expect blocked commands and wasted tokens.
- **Skill coverage is thin.** In real generations, 14% to 29% of what roles need had a matching skill, and the review screen says so. Roles without a skill rely on their instructions. Judged on a labelled set, skill matching found 33 of 44 right skills, and 19 of its 52 suggestions were wrong, so read what you approve.
- **Cost per command is high** for small jobs. See above. A two-sentence reply goes through intake, the work, QA, legal, and a final report.
- **It works with files only.** It doesn't publish, send, buy, render video, or browse the web unless you turn on search. Its output tells you what a person must check.
- **It can't check facts without sources.** With no web access, it marks claims as unverified, and leaves placeholders such as `[Bakery name]` or `[ISI PROMO]` for you to fill in.
- **Reviews aren't proof.** [The test above](docs/research/qa_gates/README.md) is small, and a weak reviewer can still approve weak work. They can also be too strict, and send back good work.
- **A command can stop at its token limit.** Rework costs tokens. In one acceptance run a small command stopped at the old 40,000 limit just short of finishing, and in the run that finished, the same command used about 37,800. That's why the default is now 60,000. Until it finishes, its work isn't exported as files. It tells you what happened and the exact lines to run: `trypro decide`, and then `/company continue <run-id>`, and it did finish when we tried it.
- **Over MCP, token limits run on an estimate** of what passes through the server, which undercounts by several times, so these limits are that estimate. Your client's own limits matter more there.
- **Keep Claude Code's permission prompts on.** Your own session only coordinates through its server, but with every permission on it also has a shell. Approving a company and answering a run's questions refuse anything but a real terminal, and a session with a shell could still change your files, including the company's records. In testing, one with every permission on tried to answer a budget question for its person, which is why those checks exist.
- **Operator mode over MCP** has one model play every role, so separation there is only procedural. `trypro agents claude-code` doesn't use it.
- **Indonesian output hasn't been reviewed by a native speaker.** Other languages haven't been tried on full commands. Retrieval queries in other languages haven't been natively reviewed either.
- **The journey ran on one Windows machine,** and on Claude Code 2.1.319. Linux and macOS have only run the test suite.

## Getting help

- **Something isn't working:** run `trypro doctor`. It says what's wrong and how to fix it.
- **To report a bug:** [open one](https://github.com/goranai/try-professional/issues/new?template=bug_report.yml), and paste the output of `trypro doctor --report`. It holds versions and check results, and no keys, paths, or names.
- **Skills for your industry:** [ask here](https://github.com/goranai/try-professional/issues/new?template=skill_request.yml).
- **Questions, and companies you've built:** [Discussions](https://github.com/goranai/try-professional/discussions).
- **To help:** [your first pull request](CONTRIBUTING.md#your-first-pull-request).

## FAQ

**How is this different from using one agent?** One agent is cheaper and faster, and for a small one-off job it's probably the better choice. Here, reviews alone took 39% to 46% of the output tokens on small commands. What that buys is a reviewer who didn't do the work, memory for each role across commands, and an audit log. No comparison of results has been measured yet.

**How is it different from a flat swarm?** In a swarm, agents work side by side. Here work flows through a hierarchy, each piece has one owner, and a role can't see another role's memory or assignments, because its server won't show them. We haven't measured whether that gives better results. That benchmark is on the roadmap.

**What does a command cost?** On a Claude plan, it comes out of your plan's usage. At API list prices, the small commands we measured came to about $0.13 to $0.66, and the largest to $1.67. Rework makes it cost more. See [the tables](#what-it-costs). A command stops and asks you at 60,000 tokens if small, or 200,000 if large.

**Which models work?** It has been proven on `claude-sonnet-5-5` and `claude-haiku-5-5`, through Claude Code. On `deepseek-v4.1-flash-1017`, the same small command finished in one run of three. No other model has been tried. See [what was tested](#what-was-tested-and-how).

## Experimental

Available, not promised. These exist and have tests, and they aren't what this release stands behind.

**The API runner,** for any OpenAI-compatible model: `deepseek-v4.1-flash-1017` is the only one tried, and the table above shows how that went. Grok and others should work the same way, and haven't been tried. Copy `.env.example` to `.env`, set `TRYPRO_LLM_BASE_URL`, `TRYPRO_LLM_API_KEY`, `TRYPRO_LLM_MODEL`, and `GITHUB_TOKEN`, and **check the model first:**

```bash
trypro check-model
trypro init "a YouTube Shorts content company"
trypro run "Make 5 Shorts about AI tools for this week"
trypro run --queue "<a command>"    # Then: trypro next
trypro resume <run-id>              # Continue unfinished work
```

`check-model` tests tool calling, strict structures, following a brief, replying in the command's language, and treating data as data. Then it has the model finish one small real command, through every review, within 40,000 tokens. It reports **suitable**, **usable with caveats**, or **not recommended**, and it can spend up to about 45,000 tokens. It's one run, so a different run can go differently.

**A look with no model at all.** A scripted model stands in, so this shows how the company works, not what a model would write:

```bash
trypro init
trypro run --dry-run "Build a health check endpoint"
trypro check-model --dry-run
```

**Other MCP clients,** such as Cursor and Codex: the server should work with them, and none has been tried. [Setting one up, and what it can't guarantee](packages/try-professional/src/try_professional/adapters/mcp/README.md).

## Roadmap

In a rough order, which [your feedback can change](docs/roadmap-v0.2.md):

- **A three-way benchmark** against a flat swarm and a single agent.
- **Quality on cheap models,** through self-refinement.
- **A Claude Code plugin,** if it can keep roles separate, to make setup shorter.
- **More runtimes.** Hermes and others. There's no Hermes adapter yet, and `skill-customs`' guide for it hasn't been tried on a real install. ["Add support for <runtime>" issues](https://github.com/goranai/try-professional/issues?q=is%3Aissue+is%3Aopen+label%3Aadapter) are open to anyone.
- **Dreaming,** where a company consolidates what it learned.

## Learn more

- [Architecture](docs/architecture.md)
- [The MCP adapter, and what it can and can't guarantee](packages/try-professional/src/try_professional/adapters/mcp/README.md)
- [Example: a YouTube Shorts studio](examples/youtube-shorts-studio/README.md), on the experimental API runner
- [Changelog](CHANGELOG.md), [contributing](CONTRIBUTING.md), and [security](SECURITY.md)

MIT licensed. No telemetry. Keys come from environment variables, and are never logged.
