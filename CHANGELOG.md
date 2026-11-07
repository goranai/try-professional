# Changelog

Each version of `try-professional`. `skill-customs` is listed only when it changes.

## 0.1.1 (not published yet)

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

- **`trypro agents claude-code --model haiku`** runs every role on one model, such as Haiku for a lighter plan. By default, each role's tier still chooses.
- **`trypro doctor --report`** prints a diagnostic to paste into a bug report: versions, system, and check results, with no keys, paths, or names.
- **Issue forms** for bugs, features, skills for an industry, and runtime support. Blank issues now point to Discussions, and security problems to private reporting.
- **"Your first pull request"** in `CONTRIBUTING.md`, this changelog, a [release checklist](docs/releasing.md), and a [v0.2 plan](docs/roadmap-v0.2.md).

### Fixed

- A `GITHUB_TOKEN` in `.env` wasn't seen when designing a company through Claude Code, so that company got no skills.

## 0.1.0 (2026-11-08)

The first public preview, for Claude Code: a company designed from one prompt, a role per subagent with its own server, memory, and skills, reviews and budgets enforced by code, and an audit log. `skill-customs` 0.1.0 was published alongside it.
