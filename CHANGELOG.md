# Changelog

Each version of `try-professional`. `skill-customs` is listed only when it changes.

## skill-customs 0.1.1 (not published yet)

Bug fixes to the scanner, from an external run over 1,449 skills on Linux. [Measured before and after](docs/research/scanner/README.md) on a labelled synthetic set: 18 of 29 verdicts right in 0.1.0, and 29 of 29 now, on a set written for these fixes.

### Fixed: false alarms

- **A frontmatter name** such as `advanced-jailbreak-chains` blocked or flagged a skill. A name now only ever gets a note, which never changes a verdict. A frontmatter description still counts, at review at most, because agents read it.
- **`curl … | python3 -m json.tool`** was blocked as running remote code. Only a pipe into something that runs what it reads counts now, and formatters such as `json.tool` and `jq` don't.
- **Documentation** blocked a skill: a heading about webhook.site, a threat catalog's table row, or a quoted example of an attack. Headings, tables, block quotes, reference lists, and quoted text now get review at most. Code blocks are never softened.

### Fixed: misses

- **Base64 or hex hiding an instruction** passed. Readable text inside them is now decoded and scanned.
- **An instruction split over a few lines** passed. The override, conceal, exfiltration, and credential rules now also run over up to 3 lines of a paragraph.
- **Cyrillic or Greek look-alike letters** hid an instruction completely. They're now folded to Latin inside a word that mixes alphabets, and the word is flagged for review.

### Changed

- **A new severity, `note`,** for findings that are recorded but never change a verdict. A lockfile written by 0.1.1 can list one.
- **The scanner is described as what it is:** a tripwire for importing third-party skills, not a full audit.

## 0.1.2 (not published yet)

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
