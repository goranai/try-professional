# Try Professional

[![PyPI](https://img.shields.io/pypi/v/try-professional)](https://pypi.org/project/try-professional/)
[![CI](https://github.com/goranai/try-professional/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/goranai/try-professional/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/goranai/try-professional)](LICENSE)
[![Python](https://img.shields.io/pypi/pyversions/try-professional)](https://pypi.org/project/try-professional/)

**Turn Claude Code into a permanent AI company: one prompt builds a hierarchy of agents with roles, memory and vetted skills. Multi-agent orchestration via MCP.**

**Preview: proven on Claude only.** v0.1.2. It works, and it has rough edges. [What was tested](#what-was-tested-and-how).

You describe a business, and Claude Code designs a company for it: departments, roles, and skills from GitHub that are checked before they're used. You approve it, and then you give it commands. It works only when you give it a command, and it remembers between commands: every role, from the CEO down, keeps its own memory and knows what it's working on.

## Who it's for

**Freelancers, agencies, and small businesses that send documents to clients or the public:** proposals, replies, flyers, newsletters, policies. Each piece of work gets a review nobody can skip, a separate legal check when it touches claims, privacy, or terms, and an audit trail you can show a client.

**For an everyday one-off task, use a single agent.** It's cheaper and faster. A hierarchy spends tokens on reviews, and that's only worth it when a mistake would reach someone outside.

## Demo

![A real run in Claude Code: setting up, designing a catering company, approving it, one command, and its deliverables](docs/assets/demo.svg)

A real run on 2026-11-08, on Sonnet through Claude Code. Waiting is cut, and each cut says how long it was. Long output is shortened, and marked. Claude Code ran non-interactively, and a script answered the real review screen, standing in for the person who approves. [How it was recorded](docs/research/demo/README.md).

## What a company makes

These are real, from [the journey](docs/research/journey/README.md). This catering company was designed from one line, "a catering business that delivers office lunches", and approved:

```text
Catering
├── Executive:  CEO (head) · Chief of Staff
├── Operations: Ops Lead (head) · Support · Menu Planner · Delivery Scheduler
├── Finance:    Finance Lead (head)
├── Marketing:  Marketing Lead (head) · Content Writer
├── Sales:      Sales Lead (head) · Proposal Writer
└── Legal:      Counsel (head) · Compliance Reviewer
```

Asked for "a weekly lunch menu and a price list for a new office client with 30 people", its menu planner wrote this. The ops lead approved it for QA, and Counsel for Legal:

```text
Mon - Lemon-herb grilled chicken, rice pilaf, green salad. Allergens: none of the 14 major allergens in the base
      recipe (check stock cube/dressing). Halal: yes if halal-certified chicken is sourced. Not vegetarian.
Tue - Vegetable lasagna with side salad. Vegetarian. Allergens: gluten, milk, egg, celery.
Wed - Chickpea & spinach coconut curry with basmati rice. Vegan, vegetarian, halal, gluten-free.
...
PRICE LIST (USD, excl. tax and delivery)
Item | Per person | Total for 30
Mon Chicken pilaf | 11.50 | 345.00
...
WEEK TOTAL | 55.50 | 1,665.00
```

It says what it doesn't know: prices are placeholders, and the allergen notes are for a kitchen to confirm. [The whole menu](docs/research/journey/deliverables/catering/large/operations-menu-planner-wp1-t1.md), and [its index](docs/research/journey/deliverables/catering/large/index.md).

It works in the command's language. For a banana-chip business in Indonesia, asked about shipping to Surabaya, a reply that names no rate it doesn't have:

```text
Halo Kak, terima kasih sudah menghubungi kami! Kami kirim ke Surabaya. Besar ongkir tergantung berat pesanan
dan kurir yang dipilih. ...
```

[The whole reply](docs/research/journey/deliverables/umkm-snacks/small/customer-care-care-lead-wp1-t1.md), and [a two-week Instagram plan in Indonesian](docs/research/journey/deliverables/umkm-snacks/large/marketing-campaign-writer-wp1-t1.md).

## Quickstart

For Claude Code. You need Python 3.10 or newer, and Claude Code signed in. It uses your own Claude plan, with no API key, and each role runs as its own subagent.

**1. Install it, and make a folder for your company.**

```bash
pip install "try-professional[mcp]"
mkdir my-company && cd my-company
```

**2. Set it up for Claude Code.** This checks Python, Claude Code, and your GitHub token, says how to fix anything missing, and adds Try Professional to Claude Code for this folder.

```bash
trypro setup claude-code
```

If it says `GITHUB_TOKEN` isn't set, set it before you start Claude Code. Without one a company gets no skills. With the GitHub CLI, that's `export GITHUB_TOKEN=$(gh auth token)`.

**3. Describe your business.** Claude Code designs a company and chooses skills, and then gives you a proposal id.

```bash
claude "Use the trypro tools to design a company for a catering business that delivers office lunches."
```

**4. Read the proposal, and approve it.** A person has to do this, in a terminal. No model can. Approving also creates the company's roles in Claude Code. The first approval downloads a local embedding model, about 0.22 GB. Your text is embedded on your machine and isn't sent anywhere.

```bash
trypro init --review <proposal-id>
```

**5. Start Claude Code again in this folder, and give the company a command.** When it asks whether you trust this folder, say yes. Otherwise each role's server can't start, and `/company` stops rather than do a role's work itself.

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
trypro agents claude-code                     Rewrite the roles for Claude Code, after the company changes
```

## Build on Try Professional

A company can be a part of your own agent. The Python API is **stable** across 0.x minor versions; the MCP tools and the adapter interface are **experimental**. [What's stable, the Claude Code path, custom roles, and extending it](docs/building-on.md).

```python
from try_professional import Company

company = Company.create("acme", name="Acme")  # the default company: no model, no cost
company.add_role(
    "marketing",
    "newsletter-editor",
    title="Newsletter Editor",
    mission="Write the weekly customer newsletter.",
    skill_needs=["write email newsletters"],
)
company.setup_claude_code()  # then, in Claude Code: /company <goal>
```

[`examples/claude-code-agent`](examples/claude-code-agent/) has a `delegate()` tool your agent can call, to hand a goal to the company through Claude Code and read back what it made. Approving a company the AI designed, and answering a run's questions, stay with a person.

## Multi-agent orchestration for Claude Code

Each piece of work has one owner, gets reviewed, and leaves an audit log you can verify. Each role runs as its own subagent, which sees only its own assignment, memory, and skills. The rules that matter are enforced by code, not by asking a model nicely.

- **A company from one description**, adapted from five industry templates, in any language. It proposes, and a person approves.
- **Skills from GitHub, vetted first.** `skill-customs` pins each one to a commit and checks its license and content, and a model judges which ones fit each role. It can also be used [on its own](packages/skill-customs/README.md).
- **A permanent company.** Roles have their own memory, departments and the company have shared memory, and later commands get the finished work of earlier ones.
- **Reviews that can't be skipped.** QA reviews everything. Legal review is triggered by what the work is, whatever a model declares.
- **Rules held by code.** Work must report on every item of its brief. Reviews can't approve with blockers. Unmet criteria and unverified claims reach you even if a final report leaves them out. Every command stops at a token limit and asks you, by default 60,000 for a small one and 200,000 for a large one on the API runner, and 150,000 and 500,000 over MCP, where usage is an estimate. It also stops, and says why, when a role keeps spending with nothing moving. [Tests play a misbehaving model](packages/try-professional/tests/test_misbehaving_model.py) against these.
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
3. **Roles.** Approving, in a folder `trypro setup claude-code` set up, writes one subagent per role. Each has no shell and no file tools, only a server bound to that role. `trypro agents claude-code` writes them again after a company changes.
4. **Commands.** Your session only coordinates. It can see who's next, and it can't read an assignment or submit anything. A small command goes straight to one role, and a large one is planned by department heads.
5. **Memory.** Roles keep their own memory, and later commands get the finished work of earlier ones.

## What was tested, and how

| Model | How | Result |
|---|---|---|
| `claude-sonnet-5-5` for every role, and `claude-haiku-5-5` for matching skills | Claude Code 2.1.319 on Windows 11, through MCP, each role as its own process with only that role's server | 9 of 9 commands finished, on 3 businesses. All 108 artifacts were recorded as coming from the right role's own server. [Details](docs/research/journey/README.md) |
| `claude-sonnet-5-5` for every role | `/company` in one Claude Code session, each role as a subagent, on a business kept out of all training | One prompt to a company, a command, and a later related command that followed the first. All 23 artifacts came from the right role's own server. It passed on its sixth run, and the fifth stopped at the token limit. [Every run](docs/research/journey/README.md#acceptance-journey) |
| `claude-haiku-5-5` for every role, and for the session | `/company` in Claude Code, on the acceptance journey, twice | **0 of 6 commands finished.** Each stopped for a person after its rework limit, or because earlier work wasn't approved. Isolation held: 90 artifacts, none claimed. Its reviewers caught 22 of 22 seeded flaws, and sent back 2 of 4 clean documents. [Details](docs/research/haiku/README.md) |
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

**Small commands cost about half as much since 0.1.2.** A small command that one role does no longer gets a final report from the CEO, or a legal review of that report. In 2 of the 3 measured below, Legal had sent that summary back twice, after approving the work itself. QA, and Legal on the work, still run. The same 3 commands, on fresh copies of the same companies, measured the same way as the first table:

| Command | Version | Model calls | Input | Cache write | Cache read | Output | List price |
|---|---|---|---|---|---|---|---|
| Catering: two-sentence customer reply | 0.1.1 | 10 | 68 | 52,362 | 178,415 | 10,986 | $0.34 |
| | 0.1.2 | 4 | 30 | 20,017 | 83,794 | 3,779 | $0.13 |
| Shorts: one title | 0.1.1 | 10 | 62 | 42,381 | 156,052 | 8,005 | $0.27 |
| | 0.1.2 | 4 | 26 | 17,033 | 67,547 | 2,811 | $0.10 |
| Indonesian snacks: one customer reply | 0.1.1 | 6 | 38 | 26,900 | 98,229 | 5,209 | $0.17 |
| | 0.1.2 | 6 | 36 | 25,166 | 88,716 | 4,624 | $0.16 |

The last one has a real round of rework on its work in 0.1.2, so it saved little. One run of each, so expect some spread. [Details](docs/research/small_commands/README.md).

A hierarchy spends more than one agent would. On the first table's small commands, reviews alone took 39% to 46% of the output tokens. No comparison with a flat setup has been measured yet. [The benchmark](docs/research/benchmark/README.md) is designed, reviewed for bias, and frozen, and it will be run in a later version.

On DeepSeek through the API runner, the one small command that finished took 25,020 tokens: [details](docs/research/journey/check_model_deepseek.json).

## What it needs

- **Python 3.10 or newer.**
- **Memory:** about 750 MB for a typical command with 22 roles, because the company shares one embedding process. The most measured in a real command was 829 MB. Having every role's server open at once takes about 2.7 GB. [Measured on Windows 11](docs/research/memory/README.md). It waits until 1.8 GB is free before starting, and that includes a 1 GB safety margin. Change it with `TRYPRO_MEMORY_MARGIN_MB`.

## Known limits

- **It's only been proven on Claude.** On a cheap model, expect blocked commands and wasted tokens.
- **Skill coverage is thin.** In real generations, 14% to 29% of what roles need had a matching skill, and the review screen says so. Roles without a skill rely on their instructions. Judged on a labelled set, skill matching found 33 of 44 right skills, and 19 of its 52 suggestions were wrong, so read what you approve.
- **Cost per command is high** for small jobs. See above. Since 0.1.2, a small command that one role does goes through intake, the work, QA, and Legal if its triggers apply, and no longer a final report and its review.
- **It works with files only.** It doesn't publish, send, buy, render video, or browse the web unless you turn on search. Its output tells you what a person must check.
- **It can't check facts without sources.** With no web access, it marks claims as unverified, and leaves placeholders such as `[Bakery name]` or `[ISI PROMO]` for you to fill in.
- **Reviews aren't proof.** [The test above](docs/research/qa_gates/README.md) is small, and a weak reviewer can still approve weak work. They can also be too strict, and send back good work.
- **A command can stop at its token limit.** Rework costs tokens. In one acceptance run a small command stopped at the old 40,000 limit just short of finishing, and in the run that finished, the same command used about 37,800. That's why the default is now 60,000. Until it finishes, its work isn't exported as files. It tells you what happened and the exact lines to run: `trypro decide`, and then `/company continue <run-id>`, and it did finish when we tried it.
- **Over MCP, token limits run on an estimate.** The server counts what passes through it, and multiplies by 3.3, because that's how much more Claude Code really used across 17 measured commands, from 2.3 to 4.6 times. [How it was measured](docs/research/mcp_estimate/README.md). So the limits over MCP are 150,000 and 500,000, and the API runner, which counts real tokens, keeps 60,000 and 200,000. `trypro status` shows both. Your client's own limits matter more over MCP.
- **Keep Claude Code's permission prompts on.** Your own session only coordinates through its server, but with every permission on it also has a shell. Approving a company and answering a run's questions refuse anything but a real terminal, and a session with a shell could still change your files, including the company's records. In testing, one with every permission on tried to answer a budget question for its person, which is why those checks exist.
- **Operator mode over MCP** has one model play every role, so separation there is only procedural. `trypro agents claude-code` doesn't use it.
- **Indonesian output was read by the owner, a native speaker,** and that's one reader. Other languages haven't been tried on full commands. Retrieval queries in other languages haven't been natively reviewed either.
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

**What does a command cost?** On a Claude plan, it comes out of your plan's usage. At API list prices, the small commands we measured came to about $0.13 to $0.66, and the largest to $1.67. Rework makes it cost more. See [the tables](#what-it-costs). A command stops and asks you at 150,000 tokens if small, or 500,000 if large, over MCP, and at 60,000 and 200,000 on the API runner.

**Which models work?** It has been proven on `claude-sonnet-5-5` through Claude Code, with `claude-haiku-5-5` matching skills. **With every role on Haiku, it finished none of 6 commands.** Its rules held, and its reviewers caught every seeded flaw, but its writers couldn't get work past those reviewers. [Details](docs/research/haiku/README.md). On `deepseek-v4.1-flash-1017`, the same small command finished in one run of three. No other model has been tried. See [what was tested](#what-was-tested-and-how).

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

- **Finishing the three-way benchmark** against a flat swarm and a single agent. [Its design is frozen](docs/research/benchmark/DESIGN.md).
- **Mixed models per role,** such as Haiku for reviewers and Sonnet for the work, measured against all Sonnet and all Haiku. [Its design is written](docs/research/mixed_models/DESIGN.md).
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
