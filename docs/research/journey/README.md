# The permanent-company journey

Does a company generated from one description do work across several commands, and remember? Run on 2026-11-07, on Claude, through the MCP adapter. **This proves the system and the MCP path. It doesn't say anything about quality on a cheap model.**

## How it ran

`run_claude_code.py` drives the real MCP server as a coordinator. Each role runs as its own `claude -p` process, with no built-in tools and only a server bound to that role. The script checks each process's real tool list, and stops if a role could reach anything else. It uses a Claude plan, and refuses to start if an API key is set.

- **Models:** `claude-sonnet-5-5` for every role, and `claude-haiku-5-5` for judging skills. Claude Code 2.1.319, Windows 11.
- **Each business:** a large command, a small one, and a later one that depends on the first.
- **Approving a company:** a script stood in for the person, and the record says so.

## Results

9 of 9 commands finished. All 108 artifacts were recorded as `attested_by: runtime`, which only a role's own bound server can record, and none as `claimed`. No process ever had a tool outside its server. Raw numbers are in `results_claude_code.json`, and the work itself is in `deliverables/`.

| Business | Command | Status | Assignments | Rejected outputs | Peak RAM of the company's processes |
|---|---|---|---|---|---|
| Catering | Weekly menu and price list | Done | 6 | 0 | not recorded yet |
| Catering | Two-sentence reply about vegetarian meals | Done | 6 | 0 | not recorded yet |
| Catering | Next week's menu, with no repeats | Done | 4 | 0 | not recorded yet |
| Shorts | 5 Shorts about AI tools | Done | 20 | 3 | 829 MB |
| Shorts | One title | Done | 6 | 0 | 828 MB |
| Shorts | 3 more, building on last week | Done | 24 | 3 | 829 MB |
| Indonesian snacks | Two-week Instagram plan | Done | 6 | 0 | 825 MB |
| Indonesian snacks | One reply about shipping | Done | 6 | 0 | 824 MB |
| Indonesian snacks | The next two weeks | Done | 6 | 0 | not comparable: only the resumed part |

Peak RAM here includes the embedding service, and not Claude Code itself. Catering ran before the shared service and the token limits were added, and the pipeline's rules were otherwise the same.

## What the work looked like

Read by the person building this, not by anyone independent, so treat it as an opinion.

- **Honest about what it isn't.** Asked to "make 5 Shorts", the CEO's report starts "I did not make 5 finished Shorts", and lists scripts and briefs, because the company can't film. It marks those success criteria as not met.
- **It doesn't invent.** Scripts leave demo results as bracketed placeholders. Prices are marked as placeholders, and allergens as estimates a kitchen must confirm.
- **It remembers.** Catering's next menu shares no dish with the last one, by reading it. The Indonesian plan's Days 15 to 28 build on specific earlier days. Shorts' "build on what worked" found no performance data, and said so rather than making some up.
- **It follows the command's language,** with `working_language: auto`, for Indonesian commands in the Indonesian company. Its success criteria were still written in English. **A native speaker hasn't reviewed this output yet.**
- **Generic in places.** With no real facts about a business, the work is careful, and much of it is placeholders.

Reviewers approved everything in this final pass, so it doesn't show reviews catching a problem. They did send work back on earlier passes.

## What the journey found

Each of these is fixed, with a test that would have failed before:

| What happened | Fix |
|---|---|
| A company couldn't find last week's menu, so QA rightly refused to approve "no repeats" | Each command now gets the finished work of up to 5 earlier commands, read-only. This doesn't depend on what a model wrote down |
| A scriptwriter was told to use an approved list it had no way to see | A role now gets the approved results of every package its work depends on |
| Plans demanded web sources a company without the web couldn't get | Planners are told what the company can reach, and claims are marked unverified |
| Over MCP, a role's notes were dropped, so it remembered nothing of its own work | They now reach its memory |
| One embedding model per role meant about 650 MB each | One process per company. See `../memory/README.md` |
| A small command blocked at 212,016 tokens | Every command has a limit by default |
| On Windows, a `.cmd` wrapper cut arguments at a newline, and dropped the flags that isolate a role | The driver runs the program itself, and checks each process's tools |
| **The first acceptance run was a fake hierarchy.** The files `trypro agents claude-code` generates gave each subagent a server named like the session's, and Claude Code used the session's. One CEO subagent did the copywriter's, QA's and legal's work, and all 14 artifacts were recorded as `claimed` | The session now gets a coordinator's server, which can't do a role's work. The acceptance script fails if any work is claimed. `results_acceptance_first_attempt_failed.json` is the record |

## Acceptance journey

One prompt to a company, one command to a result, and a second related command later, the way a Claude Code user would run it: `run_acceptance.py`, on a business kept out of all training.

**Not finished yet.** The first attempt found the fake hierarchy above, so it doesn't count. Claude Code's memory safeguard stopped the second during the company's design.

## Reproduce

```bash
python docs/research/journey/run_claude_code.py <output_dir> [business ...]   # Uses your Claude plan
python docs/research/journey/run_acceptance.py <output_dir>                   # Uses your Claude plan
python docs/research/journey/collect_results.py <output_dir> docs/research/journey
```
