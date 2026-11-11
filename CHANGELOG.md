# Changelog

Each version of `try-professional`. `skill-customs` is listed only when it changes.

## 0.7.2 (2026-11-11)

A company should get lighter the longer it runs, not heavier. That's where it can do what one agent can't, because one agent has no record. This version starts on it, and finishes the push check from its fifth review.

### Added

- **Trust earned by reviewed work.** A role whose last 5 reviewed hand-ins were all approved skips the QA review of its plain work, except one hand-in in 3, which is still reviewed. Anything public, touching a license or personal data, or sent back once, is always reviewed, and one review that asks for changes ends the trust until it's earned again. Set it in `pipeline.trust_after` and `pipeline.spot_check_every`; `trust_after: 0` turns it off. Reviews alone took about half of the output tokens on the small commands measured, 42% to 59%. This also corrects the README, which said 39% to 46% with no data behind it.
- **Whether it's getting lighter, after every command.** The report shows tokens per task over the company's recent commands, oldest first, and whether that went down or up. Over MCP, the coordinator gets the same line to pass on. Skipped reviews are counted in the report too.
- **Lessons that save steps.** Roles are asked for the lesson that would have saved them steps (staff playbook 9), so what one task found out, such as where something is or how to check it, is recalled next time instead of found again.

### Fixed

- **More ways of running a hidden push now ask a person:** a string piped into a shell or python (`echo '…push' | bash`), `bash <( )` and `source <( )`, awk, PowerShell given the command as a plain string, any spelling of PowerShell's `-EncodedCommand` (such as `-en` or `'-enc'`), ANSI-C quoting that spells git in escapes, `p{u,}sh`, and git's own programs run directly, such as `git-subtree push`.
- **Checking a long command stays fast, all of it.** 0.7.1 made the push check fast, but the check for `gh api` writes and `curl … | sh` still took up to 2 seconds on a 50,000-character command. Now each takes time in step with the command's length.

### Changed

- **What 0.7.1 said, said exactly.** It listed some forms that still got through: a string piped into a shell, the other spellings of `-EncodedCommand`, and escapes that spell git. They're fixed above. A command that only mentions push, such as `git log --grep=push`, still doesn't ask, though a few more read-only commands with push in them now do, such as one that uses awk. A word built at run time, such as `pu${x}sh`, can still get through: this guards against mistakes, and isn't a sandbox.

## 0.7.1 (2026-11-11)

From the fourth review of 0.7.0's push check, by the company of agents that reviews this project: it confirmed real pushes that still ran without asking, and two inputs that took over a second to check.

### Fixed

- **A push run some way the check can't read asks a person.** With the word push in a command, any of these now needs approval: eval, a string run by a shell or by python, node, perl, ruby or PowerShell, a here-doc, trap, an alias, xargs, env -S, `$( )` or backticks, brace expansion, and ANSI-C quoting. A command given encoded, as to PowerShell, always does. A command that only mentions push, such as `git log --grep=push`, doesn't.
- **Checking a long command stays fast.** No input up to the 50,000 characters it reads takes a second.

### Changed

- **What 0.7.0 said about pushes, said exactly:** the check guards against mistakes, and asks a person. A push spelled out at run time, such as from a script file, can still get through. It isn't a sandbox, and in Claude Code a role's shell follows your own permissions.

## 0.7.0 (2026-11-11)

An agent, but a company. The first version built from three sources at once: a real company's runs, a lab where companies of agents work on real bugs with hidden tests, and a long argument about why anyone should use this at all. It's also the first one a company of agents reviewed before release: its review found the first fix for pushes too slow and too easy to get around, before it shipped.

### Added

- **Quick one-offs are done alone.** A question whose answer stays in the chat and is cheap to get wrong, such as a fact to look up, the front desk answers itself, with its own tools, and says so. Anything that goes out to someone is still the company's.
- **A lesson its head drops goes to you.** A department head can't drop a teammate's lesson for good: it joins the company memory waiting for you, saying who proposed it and who dropped it. Kept, it's remembered by the whole company, not only that team. In a real company, a head dropped a lesson about work it doesn't do, and the same mistake came back.

### Fixed

- **More pushes need a person's approval** on the company's own engine. A real role's `git -C <clone> push` ran without asking, because only the plain `git push` was matched. The plain form is still matched anywhere in a command, and the command is also read word by word, with each line, backtick and `$( )` as a command of its own, and quotes and escapes taken out first, so `git -C <repo> push` on any line, `git -c ... push`, a quoted or partly quoted push, `git.exe`, `send-pack`, `subtree push`, a push inside `bash -c` (with any flags) or `eval`, an alias set on the command line, and a subcommand in a variable are caught too. Checking stays fast on long commands. A push built up from pieces can still get through: this guards against mistakes, and it isn't a sandbox. In Claude Code, a role's shell follows your own Claude Code permissions.
- **A role with none of its tools made its own.** Claude Code leaves out each role's tools until the folder is trusted, `claude -p` included, and a real CEO wrote its own client for its server instead. A role now stops with `NO_ROLE_TOOLS`, and the coordinator tells you what to do.
- **A role refused `workspace_write` didn't know what to do next.** It's told to put it in its submission.
- **A missing GitHub token stopped the first run.** Skills are optional: it says so and goes on.

### Changed

- **Roles run the checks their work touches as they go, and all of them once, at the end.** In the lab, agents ran full test suites again and again. The staff playbook stays about the same size.
- **One line says what it is:** "An agent, but a company."

## 0.6.5 (2026-11-10)

What a company keeps, without a person typing commands for it. From the same real company: its person repeated the same rules in every command, a skip list of 15 repositories among them, and seven lessons its CEO proposed waited at a terminal nobody knew about.

### Added

- **Standing rules.** Say in Claude Code that something should always or never happen, and it offers to make it a rule; once you confirm, every role sees it on every step. `company_rules` lists them for the session, and `trypro rules`, `trypro rules add`, and `trypro rules remove` work at a terminal. Together they're at most 3,000 characters, because they're sent on every step.
- **The CEO's lessons are put to you in the chat.** What it proposes the whole company remember is shown at the end of the command, and kept or dropped as you say, with a confirmation, instead of waiting for `trypro memory review`.

## 0.6.4 (2026-11-10)

From a real company's first day: a contribution company, six hunts on Claude Code, three bug reports posted. Its CEO sized every hunt for one department, so the Validator and the Poster it was designed with never ran, its reviewer sent the same work back for a check only the Validator could make until a person stopped the run, and a person posted every finding by hand, so nothing it put out was tracked.

### Added

- **A company grows when its work shows it should.** The CEO proposes a role, or a new department and its head, at intake when part of a command fits no role, and at the end of every large command and of any small one that was sent back, escalated, or handed on. It sees every department, and the record: work sent back and why, departments with no work across recent commands, and work put out by someone who isn't one of its roles. It proposes only on that evidence. A person decides, in Claude Code's chat, or with `trypro org review`; `trypro org proposals` lists them. An added role is checked like any other, and undone if it would break the company.

- **You answer in the chat.** When a command stops for you, or the CEO proposes a role, Claude Code asks you there, and records what you say with a new tool that it asks you to confirm on every call, in every permission mode, bypass included: an `ask` rule in `.claude/settings.json`. Only the coordinator's server has these tools; a role's server, or another client acting as every role, doesn't. `trypro decide` and `trypro org review` still work at a terminal.
- **Claude Code's files catch up after an upgrade by themselves,** the next time it starts, keeping each role's model. No `trypro agents claude-code` to remember.

### Fixed

- **A command that needs a second department was sent to one.** When a small command's own criteria name a role of another department, such as "confirmed by the validator", it's planned instead, with a constraint saying why. Replayed on the real company's charters, five of its six hunts would have been.
- **A reviewer sent work back for what its author couldn't fix:** another role's check, or facts that don't exist. It blocks instead, which takes it to whoever can arrange it.
- **What a person posts from Claude Code wasn't tracked.** The front desk now records it with `outcome_record`, credited to the role whose work it was, so maintainers' verdicts still reach that role.
- **Rewriting Claude Code's roles reset each role's model.** A role a person put on Opus by hand keeps it. `--model` still sets every role.
- **Roles in Claude Code ran test suites with 8 workers at once** on a machine with 1.6 GB free. They're told the machine is shared: one heavy command at a time, at most 2 workers, with a time limit.
- **Designing a company over MCP waited for memory in silence,** for half an hour once. It says how much memory it needs instead, and nothing has started.
- **The review screen said a company only produces files,** and that skills with scripts can't run because "no role has a shell". Roles have had a shell since 0.5.0. It says what roles can do now, and why someone else's scripts aren't installed.

## 0.6.3 (2026-11-10)

Haiku runs a company. Seven everyday jobs, three times each, through Claude Code with every role on `claude-haiku-5-5`: **21 of 21 finished**, from 1.3 minutes and $0.014 for a customer reply to 5.2 minutes and $0.11 for content that needs research, at list price. Two earlier rounds finished 16 of 19 and 19 of 21; every run that didn't finish was read, and its cause fixed. [Every run](docs/research/efficiency/README.md#063-on-haiku-through-claude-code-21-runs-21-finished).

### Fixed

- **Reviews didn't converge.** Each review of reworked work started over and found a new list of issues, round after round, until the rework limit. A reviewer now sees its own earlier verdict, lists everything the first time, and adds only new major issues after.
- **Reviewers asked for what no answer could give.** They sent back [placeholders] and phrases like "made with care" as unsourced facts, while asking for captions written out in full. Now only specific, checkable claims, such as a number, date, price, name, or award, need a source or a placeholder; ordinary description isn't a claim; and a placeholder for a fact the brief didn't give is the right answer.
- **Checks a workspace can't do,** such as how a page prints, were sent back again and again. They pass when marked unverified for a person.
- **A reviewer mistyped what it was reviewing,** a 24-character id, six times, and the run stopped. Code fills in what a review is about, so a verdict only ever applies to the work the reviewer was shown.
- **A small model's own wording:** criteria restated in its own words now count when they're clearly the same, or in the same place in a full list; fields of its own, such as `notes_placeholder` or a reviewer's `checked`, are dropped, so they still can't take effect; a hand-in wrapped in another `payload` is unwrapped; a severity of "note" is minor; and an escalation answer that activates no department in particular asks for nothing.
- **A tool that broke took the whole command down.** A URL with a search query in it crashed a real run. A broken tool call now costs that one call, and such a URL is refused with a reason the role can act on.
- **The gateway's vague 400 ended steps** it would have let through a little later. It's waited out, and the step can be retried.
- **A worker drifted.** A real one given 40 turns spent them on another repository's branches and the same check four times. At turns 15 and 30, a role is reminded to finish what's missing, with no new directions. Nothing is cut off.
- **A duplicate went unnoticed.** A held issue about a broken link duplicated an open pull request worded differently. Before anything new is posted on GitHub, the search now also looks for the exact link, path, or code it's about, in issues and pull requests.

## 0.6.2 (2026-11-09)

Specialists, without the group-chat failure. Each role keeps its own job and memory, a person talks only to the CEO, and work isn't passed around for nothing.

### Added

- **Measured on real jobs.** On `step-5-preview`, five everyday commands took 4.1 minutes and 133k tokens in all, against 8.6 minutes and 316k on 0.6.1: faster on every one. A three-department content job finished, where 0.6.1 was blocked. One repository job was slower. One run each, so read [the write-up](docs/research/efficiency/README.md) before quoting it.
- **Every run says what it cost in time.** After each command, and in `trypro status <run-id>`: minutes of work, apart from minutes waiting for a person, model calls and tokens by step and by role, and the extra rounds: rework, refused hand-ins, failed steps, escalations, and handoffs. `Company.efficiency(run_id)` returns it. It's measured from the audit log alone, so runs by earlier versions can be measured too. [Before and after, on real jobs](docs/research/efficiency/README.md).

### Changed

- **The fewest roles that can do it well.** The CEO now sends a command to one department, with one role or several of its own, unless departments must each do part of it; when unsure, to one department. It used to send anything unclear through the whole company. A department head gives one role one task when it can do it all, and the CEO doesn't split what one department can do, or activate a department that would only pass work on.
- **A handoff says why.** A head asking another department for work must say what that department has that its own doesn't, in a new `because` field. Work can't be handed back to a department that asked for it, and a handoff of a handoff is the last: past that, the work is done or escalated.
- **Roles work at the same time in Claude Code.** `/company` starts the subagents of every role that's ready at once, one per role, instead of one after another. The company's own engine already ran independent work in parallel.
- **No token budgets asked for in plans.** The CEO was told to give each work package a token budget, which nothing enforced.

### Fixed

- **A command could hang a run for good.** On a real run, a role's shell command started a program and hit its 5-minute limit; the shell was stopped, the program kept its output open, and the run waited 90 minutes with nothing happening. Now the whole process tree a command started is stopped at the limit.
- **Roles in Claude Code still started one by one with a small model.** With every role on Haiku, the coordinator started each subagent in its own message, so two departments' heads worked one after the other, though their writers and reviewers did overlap. Now, when several roles are ready, `run_next_action` itself tells the coordinator to start them together.
- **A step's conversation grew with every page a role read.** In a real multi-department job, an analyst reading web pages sent 2.8 million input tokens in 22 calls, because every output was sent again with every call, and the run ended in a gateway error. Now a step keeps its 6 newest tool outputs whole and cuts older ones to their start, in batches, so a provider's prompt cache still works. A role can run a command again if it needs all of it.
- **Workers and reviewers re-checked what was already checked.** A real job's worker verified everything twice, and its reviewer re-ran every search: 73 calls. Workers now stop when the definition of done is met, and a reviewer of computer work re-runs only the one or two commands its verdict rests on, within 16 turns.
- **More wording in the same real run:** a task with no id is named from its title, review severities such as "nit", "info", "critical", or "moderate" are understood, and a reviewer's own summary or list of checks no longer refuses its verdict.
- **A whole payload sent as JSON text** was refused three times in a row, and a criterion marked `"status": "met"` or `"achieved": "yes"` wasn't understood. Both are now. Anything unclear, like "mostly", is still refused. A refused hand-in is kept in the audit log with its payload first, in full, so the next wording can be seen.

## 0.6.1 (2026-11-09)

A company learns from what happened to its work, and works on its own. Nothing here is for one kind of business: whatever a company puts out, it can follow.

### Added

- **Outcomes, for any kind of work.** What a company sends out, such as a pull request, a post, a quote, or an email, is tracked with who it went to: a repository, a client, a site, anything a role names. A link printed by an outside command a person approved is tracked by itself, and in Claude Code a role gives a link or any reference to the new `outcome_track` tool. `trypro outcomes` lists them.
- **Verdicts with reasons.** Accepted, rejected, duplicate, by design, low impact, or ignored, with what was said. A person gives one with `trypro outcomes set`, or `/outcome 3 rejected "too expensive"` in chat. Where code can read one, a checker does, with no model: GitHub issues and pull requests through `gh` ships. It reads GitHub's record, labels, and a maintainer's last words, so "working as intended" is by design and "too minor" is low impact, links a duplicate to what it duplicates, and calls 30 days without a reply ignored. A passer-by's comment on accepted work isn't kept.
- **Learning from them.** Each role that worked on it gets the lesson, with the reason, in its own memory, and each department head decides whether the department keeps it. A command that names an earlier command's run credits that run's roles too, so a finder hears what came of what someone else sent. Roles that plan, assign, do, or check work are shown the record, and whatever their work names comes first: that target's history, and what it doesn't want.
- **Checks before anything goes out.** Whoever decides on an outside command, at the prompt or later, and the reviewers, are shown what the company already sent to the same target, and what came of it. Searches can plug in beside that; the one that ships looks for issues a new GitHub issue may duplicate.
- **Follow-ups.** A new reply from someone else queues a command to read the thread and answer only if an answer is needed.
- **Schedules.** `trypro schedule add <name> "<command>" --every 1d`. `trypro schedule run` does one round: it checks outcomes, runs what's due, and runs follow-ups. Commands a person queued still wait for `trypro next`. `--watch` keeps going, and `trypro schedule install --apply` has Windows' Task Scheduler or cron run the rounds. A schedule missed while the machine was off runs once, not once for each time it missed. With a Telegram bot set up, a round's summary is sent there.
- **Approvals that wait.** With nobody to ask, a command that changes something outside, such as `gh issue create` or `git push`, is held instead of refused, and the run carries on. `trypro held`, `trypro held approve <number>`, and `trypro held deny <number>`; or `/held`, `/approve 3`, and `/deny 3` in `trypro chat` and on Telegram, with `/outcomes` and `/schedules`.
- **Company design knows about these.** For a business that puts work out where others answer, it's asked to give one role the posting and one the following up, and to keep finding, checking, and putting out with different roles where a mistake would be public.

### Fixed

- **Roles were told they had no shell.** Since 0.5.0, roles on the API runner have had a shell and the web, but their assignment still said "no repository or shell", and planners were told nothing could run a program. Both now say what roles can really do, on both engines. A role server in a company set up with `--sandboxed` says it has no computer.
- **A gateway that refused markdown headings.** `step-5-preview`'s gateway refused a real assignment every time, with no reason, and took it word for word with plain "Heading:" lines in place of "## ". After a vague refusal, the client now tries the instructions in the first message, and then plain headings, and keeps the shape that works.
- **A held step looped.** In a real run, reviewers kept sending work back because the issue didn't exist yet, when posting it was the step waiting for a person, and the worker held the same post again in other words. Now workers, reviewers, and the final report see the run's held commands, and a step whose exact command is held counts as done for that run, if the command is right. Planners are told to write criteria a held step can meet.
- **Work on a computer was called stuck while it was still working.** A real role looking for a mistake in a repository ran out of its 12 turns, and loop protection counted its commands as nothing happening. Roles that do or check work now get 40 turns, and a command, page, or search a role hadn't already done in that step counts as work in progress. The same call again and again still counts as a loop.
- **What a role ran wasn't on the record.** The audit log now keeps each shell command, fetched page, and search.
- **A tool named "function=shell"** by one model was refused as unknown. Prefixes like that are dropped.
- **A reply that ran out of room failed the step.** Every reply had 16,000 tokens, and a reasoning model's thinking counts toward that. Now a reply that's cut off is asked again with twice the room, up to 128,000, which is what `step-5-preview` can write. A model that allows less says so, and its own maximum is used from then on. `TRYPRO_LLM_MAX_OUTPUT_TOKENS` sets where it starts.
- **More of a model's own words for the same thing,** each of which refused finished work in a real run: `cmd` for a test's command, `summary` for a review issue's description, `definition_of_done` for acceptance, and `did_not_do` in a role's notes. A role's notes or language put inside its hand-in are moved out or dropped. An unknown key in a role's notes is dropped, where it used to cost the work. Flags in the wrong place are still refused, because they decide which reviews run. A refused hand-in is now kept in the audit log, to see why.
- **A manager's answer to an escalation was refused over its wording,** twice in one real run: "department" for a department's id, and one acceptance criterion written as text instead of a list. Those are understood now, and a new work package with no id is named from its title.
- **The README** still said the API runner had no shell, and listed the old token limits in its FAQ.

### Changed

- **Company design has no token limit,** in line with 0.6.0. It stops after 3 attempts, as before.
- **Outcomes, held commands, and schedules** are kept in `.trypro/outside.sqlite`. Nothing else in a company's data changes.

## 0.6.0 (2026-11-08)

Cheap models finish their work. On `step-5-preview`, through the company's own engine, real jobs went from 2 of 5 finished to 4 of 5, including one that used `gh` in the shell and one in Indonesian. [How it was measured](docs/research/cheap_models/README.md).

### Changed

- **Hand-ins are forgiven their wording, and never their content.** Cheap models name fields their own way: `text` for `claim`, `issue` and `fix` for a review's `description`, or an extra `status`. Known alternative names are mapped, unknown fields inside items are dropped, and a claim with no source is marked unverified, which is what a person is told. The strict checks still run after, on both engines.
- **Gateways are waited out.** A 502 or 503 gets 5 retries, with waits up to 30 seconds. A 400 with no reason gets one retry, and then the instructions are sent at the top of the first message instead of as a system message, which one gateway refused beside some text.
- **No token limits by default.** A company works until the job is done. Loop protection still stops a role that goes in circles: 24 model calls with nothing moving, or 3 hand-ins refused in a row. To cap spending, set `max_tokens_per_small_command` and `max_tokens_per_run` (and the `mcp_` ones) under `pipeline.budget`. Companies that already have limits written in `org/org.yaml` keep them; delete those lines to remove them. `null` or a missing line means no limit.
- **A shell command's output is cut to its last 6,000 characters,** because every call re-sends it.

## 0.5.0 (2026-11-08)

An agent harness of its own: talk to your company, with any model.

### Added

- **Its own engine's roles have a computer too.** On the API runner, every role that does or checks work gets `shell`, which runs in its run's workspace (`git`, `gh`, `curl`, `python`), and `web_fetch`. So a company of real agents works with any OpenAI-compatible key, without Claude Code. A command that changes something outside, such as a push, a new issue or pull request, or a publish, asks a person first, and doesn't run with nobody to ask. The model's own API keys never reach a command; a GitHub token does, so `gh` works. `computer_tools: false` in `trypro.yaml` keeps a company to documents.
- **`trypro chat`: talk to your company.** Say what you want, and the CEO takes it down the hierarchy and answers. Questions and approvals are asked in the conversation, "continue" picks up a command that stopped, and it remembers the last few exchanges.
- **`trypro telegram`: the same, from Telegram.** Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_CHAT_IDS` in `.env`. Only those chats can give it work. It uses Telegram's Bot API directly, with no new dependency.

### Not yet

- **Cheap models aren't tuned.** On DeepSeek, an earlier small command finished one run in three. Tuning needs paid runs, and comes next, with the owner's approval.
- **The engine's computer was tested on a scripted model.** Its first real runs are yours.

## 0.4.0 (2026-11-08)

A company of real agents: the hierarchy decides who does what and who checks it, and every role can use a computer.

### Changed

- **Every role is a full agent in Claude Code.** Roles' subagents get Claude Code's own tools: the shell, files, `git` and `gh`, and web search and fetch, beside the server bound to their role. The flow is the same: you, the CEO, a manager, a worker, QA, and back up to you. Claude Code asks you before commands unless you allow them. To keep the old behaviour, documents only, run `trypro agents claude-code --sandboxed`. Tested once for real: a worker used `gh` to find two popular repos and a real error in each README, the research lead checked both, and the CEO reported back, for $0.34 at list price.
- **Legal is optional, and off by default.** A designed company gets a Legal department only for regulated work, such as law, health care, finance, or insurance, and the default company has none. With Legal, its review works as before. `trypro pack add legal` adds it, with its gate, and `trypro pack remove legal` removes both.

### Before you upgrade

- **Existing companies keep their Legal department,** and work as before.
- **Rerun `trypro agents claude-code`** in an existing company, so its roles get their tools and the front desk.
- **The experimental API runner** is unchanged: its roles still have only the company's own tools.

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
