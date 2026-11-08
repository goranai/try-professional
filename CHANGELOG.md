# Changelog

Each version of `try-professional`. `skill-customs` is listed only when it changes.

## 0.3.0 (2026-11-08)

Say what you want, and a company gets to work.

### Added

- **`trypro start "<what you want>"`.** From an empty folder, one command checks your setup, has Claude Code design a company that can do what you asked, shows it to you to approve, and opens Claude Code on your request. If `GITHUB_TOKEN` isn't set, it uses your GitHub CLI's token for that command only, so the company gets skills. A person still approves the company, and no model can.
- **A front desk.** The company's Claude Code files now include a part of `CLAUDE.md` that hands any request for work, in plain words, to the company, as `/company` does. Anything else you keep in `CLAUDE.md` stays. Rerun `trypro agents claude-code` in an existing company to get it.

### Before you upgrade

- **Existing companies keep working.** `/company` is unchanged.

## 0.2.1 (2026-11-08)

- **The README says who it's for:** people sending client-facing or public documents that need a review nobody can skip, and an audit trail. And that for an everyday one-off task, a single agent is cheaper and faster.
- **Uses skill-customs 0.1.3,** with fewer false alarms on downloads piped to an interpreter.

## skill-customs 0.1.3 (2026-11-08)

- **`remote-pipe` blocked legitimate commands.** It now flags a download piped into something that runs stdin as code: `| sh`, `| bash`, `| python3 -`, `| ruby`, and the like, with or without `sudo`. It no longer flags an interpreter given its own code, which reads stdin as data (`python3 -c "…"`, `node -e "…"`, `bash -c "…"`), formatters such as `jq`, a plain download with no pipe, or a health check followed by `||` or `&&` and another command. Inline code that hands stdin to `eval` or `exec` is still blocked, as is `curl … install.sh | bash`. On the labelled synthetic set: 67 of 71 verdicts right in 0.1.2, and 71 of 71 now; `remote-pipe` precision went from 0.67 to 1.00, with recall still 1.00. **Verified on synthetic fixtures only.** The 12 new cases were written for this fix. [Details](docs/research/scanner/README.md#013).
- **The ruleset is 2026.11.4.**

## 0.2.0 (2026-11-08)

Better skill matching, and a stable base to build other agents on.

### Before you upgrade

- **Existing companies keep working,** with no change to `org/` or `trypro.yaml`.
- **Skills already matched stay as they are.** The new matching applies when a company is designed, or its skills are matched again (`trypro skills match`). Judgments cached before 0.2.0 are made again once.
- **The Python API is new, and stable** from this version: `from try_professional import Company`. Anything else you imported before is internal, and may change.

### Added

- **A stable Python API,** `try_professional.Company`: create or open a company, add custom roles, validate, write Claude Code's files, run a command, and read its status, deliverables, and final report. What's stable and what's experimental is in [docs/building-on.md](docs/building-on.md).
- **An example of building on it:** [`examples/claude-code-agent`](examples/claude-code-agent/), with a company that has a custom role, and a `delegate()` tool another agent calls through Claude Code.

### Changed

- **Skill matching finds more of the right skills.** Each role's shortlist holds 30 skills, up from 20. On a fresh set of 57 public skills, labelled by a separate Claude instance and not by a person: 40 right matches against 28, and precision of 77% against 72%. [How it was measured](docs/research/skill_quality/README.md).
- **The skill judge sees the company's other roles,** from 0.1.3, measured again here with the larger shortlist.

### Not in this version

- **The three-way benchmark** against one agent and a flat team is designed, reviewed for bias by a separate Claude instance, and frozen, and its harness works. It was stopped after 9 of 54 runs, because finishing it would have used the owner's Claude plan for most of a day. **There's no result yet.** [Its status](docs/research/benchmark/README.md).
- **Mixed models per role:** [its design is written](docs/research/mixed_models/DESIGN.md), and it hasn't run.

## skill-customs 0.1.2 (2026-11-08)

Bug fixes to the scanner, from a rescan of the same 1,449 skills with 0.1.1. [Measured before and after](docs/research/scanner/README.md#012) on a labelled synthetic set: 48 of 59 verdicts right in 0.1.1, and 59 of 59 now. The set was written for these fixes, so read the "after" as each fix working, and the earlier catches holding.

**These fixes were verified on synthetic fixtures only.** The 1,449-skill library they came from wasn't rescanned with 0.1.2, so how its totals moved isn't known.

### Fixed: false alarms

- **Ordinary requests in code were blocked.** 0.1.1 read a few lines of code together, so a request's own `-d '{...}'` body, or the next command, looked like an upload. In 28 legitimate skills it did: API calls, GraphQL examples, and networking notes. Now a command continued over lines is read as one command, and nothing else. A body written out in full passes. Sending a file or a variable from code gets a review. Sending a secret is blocked: a key file, cloud or cluster config, an `.env` file, an environment variable named like a secret, or the whole environment. A token in an `Authorization` header doesn't count, because that's how an authenticated call works. Outside code, an instruction to upload a file is still blocked.
- **Text in several languages, or with maths, got a review.** Only a word that mixes Latin letters with Cyrillic or Greek look-alikes is flagged now. Symbols such as Δt, μs, or σ_x aren't look-alikes, and a word is split at digits and underscores. In the rescan, this rule made 31 findings in 11 skills, and at least 12 of the findings were maths symbols.

### Fixed: misses

- **A secret put into the agent's own reply passed,** when there was no URL: for example, an instruction to add what's in `~/.aws` to the answer, and keep it out of the summary. Two new rules block it. `exfil-reply` catches a secret's location put into a reply, and `conceal-secret` catches one kept out of what the person is told. A warning such as "never include ~/.aws/credentials in your reply" doesn't count.

### Changed

- **The ruleset is 2026.11.3.** A lockfile records it, with the scanner version.

## 0.1.3 (2026-11-08)

Bug fixes from an external retest of 0.1.2 on Linux, on the API runner and `deepseek-v4.1-flash-1017`.

### Changed

- **The skill judge sees the company's other roles.** It was told to leave out skills that belong to a neighbouring role, but never shown the others, so it guessed. On a fresh held-out set of 39 public skills, judged by Claude Sonnet, precision rose from 54% to 62%, and coverage didn't change: 56% before and 53% after, within run-to-run noise. Judgments cached by earlier versions are made again once. [How it was measured](docs/research/skill_quality/README.md).

### Fixed

- **A stuck run was noticed too late.** After a person resumed it with 250,000 more tokens, a real run stopped as stuck only at 257,476 tokens and 48 model calls. A manager's answer to an escalation, and the same work being assigned again, had counted as progress. They don't now, and a run also counts as stuck after 3 refused hand-ins, or after a whole command's limit of tokens with nothing accepted, approved, or sent back. Budget a person adds doesn't raise that. Replayed call by call on the scripted model, the same run now stops at 85,516 tokens and 19 calls. [The replay, and what it doesn't show](docs/research/stuck/README.md).
- **The draft label repeated itself, and was cut mid-word:** "NOT APPROVED: stopped at This command stopped at 63348 tokens…". It now says what the run stopped at, in a few words, such as "its token limit, at 63,348 tokens". The whole question is under "Why it stopped".
- **A name from a private skill library was in the docs and tests,** from the external scan behind skill-customs 0.1.1. It's replaced with a neutral description, and a made-up name in the tests.

## skill-customs 0.1.1 (2026-11-08)

Bug fixes to the scanner, from an external run over 1,449 skills on Linux. [Measured before and after](docs/research/scanner/README.md) on a labelled synthetic set: 18 of 29 verdicts right in 0.1.0, and 29 of 29 now, on a set written for these fixes.

### Fixed: false alarms

- **A frontmatter name**, such as a skill named after an attack technique, blocked or flagged a skill. A name now only ever gets a note, which never changes a verdict. A frontmatter description still counts, at review at most, because agents read it.
- **`curl … | python3 -m json.tool`** was blocked as running remote code. Only a pipe into something that runs what it reads counts now, and formatters such as `json.tool` and `jq` don't.
- **Documentation** blocked a skill: a heading about webhook.site, a threat catalog's table row, or a quoted example of an attack. Headings, tables, block quotes, reference lists, and quoted text now get review at most. Code blocks are never softened.

### Fixed: misses

- **Base64 or hex hiding an instruction** passed. Readable text inside them is now decoded and scanned.
- **An instruction split over a few lines** passed. The override, conceal, exfiltration, and credential rules now also run over up to 3 lines of a paragraph.
- **Cyrillic or Greek look-alike letters** hid an instruction completely. They're now folded to Latin inside a word that mixes alphabets, and the word is flagged for review.

### Changed

- **A new severity, `note`,** for findings that are recorded but never change a verdict. A lockfile written by 0.1.1 can list one.
- **The scanner is described as what it is:** a tripwire for importing third-party skills, not a full audit.

## 0.1.2 (2026-11-08)

Small fixes that matter to someone using it, and none of v0.2's features.

### Before you upgrade

- **Existing companies keep working.** New budget fields have defaults, so `org/org.yaml` needs no change. To see the new limits written out, add `mcp_max_tokens_per_small_command: 150000` and `mcp_max_tokens_per_run: 500000` under `pipeline.budget`.
- **Rerun `trypro agents claude-code`** in each company, so `/company` gets the new wording.

### Changed

- **Small commands cost about half as much.** A small command that one role does gets its final report from code, from the approved work, and no longer a CEO report and a legal review of it. QA, and Legal on the work itself when its triggers apply, still run. Measured on 3 commands: 26 model calls became 14, and $0.77 at list price became $0.39. [How it was measured](docs/research/small_commands/README.md).
- **Token limits over MCP use a corrected estimate,** the server's count times 3.3, which is what Claude Code was measured to really spend. Over MCP the defaults are 150,000 for a small command and 500,000 for a large one. The API runner, which counts real tokens, keeps 60,000 and 200,000. `trypro status` shows both. [How the factor was measured](docs/research/mcp_estimate/README.md).
- **Setup is shorter: 5 steps instead of 7.** `trypro setup claude-code` checks Python, Claude Code, and your GitHub token, and adds Try Professional to Claude Code for the folder. Approving a company there also writes its roles for Claude Code. Approval still needs a person at a terminal.
- **The README** starts with a demo from a real run, and a real company's work, and has badges. Nothing it said was removed.

### Added

- **A stuck run stops, and says why.** If tokens keep rising for 24 model calls while nothing moves, the run stops, names the role and what keeps failing, and offers to retry, ideally with another model for that role (`--model`), to waive a stuck review with a name and a reason (`--by`, `--note`), or to cancel. At a token limit, a run that's stuck gets this instead of more budget.
- **Drafts when a run stops.** Work that exists but wasn't approved goes to `deliverables/<run-id>/draft/`, labelled "NOT APPROVED: stopped at" and the reason, with what the reviewers said.
- **The review screen marks low-confidence skill matches** with `?`, and says how to drop one.

### Fixed

- Docs that later fixes made wrong: the spec said a token limit of `null` meant none, when it means the default, and the MCP adapter's guide still described the longer setup.

## 0.1.1 (2026-11-08)

Fixes for what someone is likely to hit in their first ten minutes. No new features.

### Before you upgrade

- **Rerun `trypro agents claude-code`** in each company you made with 0.1.0, then restart Claude Code. Its `/company` command and role subagents are written once, so they keep the old wording until you do.
- **Existing companies keep their old token limits.** They're written into `org/org.yaml`. To use the new ones, set `max_tokens_per_small_command: 60000` and `max_tokens_per_run: 200000` under `pipeline.budget`.

### Changed

- **Higher token limits for new companies: 60,000 for a small command, and 200,000 for a large one,** up from 40,000 and 150,000. Stricter reviews mean more rework. A small command with the 2 rework rounds the pipeline allows used 37,762, and one that stopped at 40,000 needed 54,605 to finish. A large one used 134,289 under the earlier, looser reviews. A runaway like the 212,016-token one before 0.1.0 still stops early. `check-model` keeps its own 40,000, so "suitable" isn't easier to earn.
- **A stopped command tells you what to do.** Its message says what happened, gives the `trypro decide` lines ready to run, and then the exact `/company continue <run-id>` line. It also says the count is the server's estimate, and that its work is saved.
- **A missing `GITHUB_TOKEN` is said up front.** `trypro init`, `trypro doctor`, and designing a company through Claude Code now explain how to set it before anything is spent. At a terminal, `trypro init` asks whether to go on with no skills.
- **Thin skill coverage links somewhere.** When under half of the role needs have a skill, the review screen links to a form for asking for skills for your industry.
- **`trypro doctor` works before a company exists, and on a broken one.** It no longer calls a Claude Code setup "not ready" because the experimental API runner isn't set up.

### Added

- **`trypro agents claude-code --model haiku`** runs every role on one model. By default, each role's tier still chooses. **Haiku for every role finished none of 6 commands in our test,** though its rules and reviews held. See [the Haiku test](docs/research/haiku/README.md).
- **`trypro doctor --report`** prints a diagnostic to paste into a bug report: versions, system, and check results, with no keys, paths, or names.
- **Issue forms** for bugs, features, skills for an industry, and runtime support. Blank issues now point to Discussions, and security problems to private reporting.
- **"Your first pull request"** in `CONTRIBUTING.md`, this changelog, a [release checklist](docs/releasing.md), and a [v0.2 plan](docs/roadmap-v0.2.md).

### Fixed

- **`/company continue <run-id>` could start a stray new run first.** Haiku did, before carrying on with the right run. `run_start` now picks up a run it's asked to continue, and the `/company` wording says this first.
- A `GITHUB_TOKEN` in `.env` wasn't seen when designing a company through Claude Code, so that company got no skills.

## 0.1.0 (2026-11-08)

The first public preview, for Claude Code: a company designed from one prompt, a role per subagent with its own server, memory, and skills, reviews and budgets enforced by code, and an audit log. `skill-customs` 0.1.0 was published alongside it.
