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
| A note in the wrong place, or a little too long, threw away a finished deliverable and charged for it again, until a command hit its limit | Notes in the payload move to where they belong, and long ones are cut. What reaches memory stays just as small |
| A session with every permission on used its shell to answer a budget question for its person | `trypro decide` now refuses anything but a real terminal, like approving a company, and the generated settings deny it |
| A command stopped at its limit couldn't be continued from Claude Code, and resuming with no amount would stop it again at the next token | `/company continue <run-id>`, and resuming adds half a limit |
| A final review was given only the CEO's summary, and said so, and approved | It now gets the work behind the summary |
| What approving reviewers flagged, such as a missing privacy notice, never reached a person | `index.md` lists it |
| **The first acceptance run was a fake hierarchy.** The files `trypro agents claude-code` generates gave each subagent a server named like the session's, and Claude Code used the session's. One CEO subagent did the copywriter's, QA's and legal's work, and all 14 artifacts were recorded as `claimed` | The session now gets a coordinator's server, which can't do a role's work. The acceptance script fails if any work is claimed. `results_acceptance_first_attempt_failed.json` is the record |

## Acceptance journey

One prompt to a company, one command to a result, and a second related command later, the way a Claude Code user would run it: `run_acceptance.py`, on a business kept out of all training.

**It passed on 2026-11-08, on its sixth run.** One Claude Code session, with only the folder's own files and a user's tools. It's still one run.

| Step | Status | Rework rounds | Artifacts by the right role's server | Claimed | Input | Cache write | Cache read | Output | List price |
|---|---|---|---|---|---|---|---|---|---|
| Design a company from one prompt | Done | | | | 36 | 46,635 | 471,088 | 6,075 | $0.29 |
| A one-page website proposal for a family bakery | Done | 1 | 10 | 0 | 90 | 77,480 | 743,298 | 15,794 | $0.46 |
| The homepage copy, following that proposal | Done | 2 | 13 | 0 | 130 | 106,645 | 1,063,112 | 25,436 | $0.66 |

The session's only calls were starting the run, asking what's next, starting each role's subagent, and loading a tool's definition. No subagent called anything outside its own server. Raw numbers are in `results_acceptance.json`, and the work is in `deliverables/acceptance/`.

**What the work looked like,** read by the person building this:

- **The second command really follows the first.** Its copy cites the proposal's sections, and carries the proposal's "to be confirmed" items along as unconfirmed.
- **Reviewers sent work back 3 times, for real problems.** They removed "Fresh from our kitchen" and "ready when you arrive", which nothing supported, and a privacy promise the proposal didn't make. The writer fixed them.
- **It isn't ready to publish, and says so.** Most bakery facts are placeholders, a note for the bakery sits at the top of the copy, and the privacy wording is left for a person.

**The runs before it, because they count too:**

| Run | What happened |
|---|---|
| 1 | One subagent did every role's work: the fake hierarchy above. `results_acceptance_first_attempt_failed.json` |
| 2 | Claude Code's memory safeguard stopped it during design, with free memory low. |
| 3 | The script's own mistake: `--strict-mcp-config` kept each role's server from connecting. No work ran. |
| 4 | Both commands finished, on the code before the stricter reviews. It showed a final review could only read a summary, and that a person wasn't told where the results were, or what approving reviewers had flagged. All are fixed. Its token split was wrong and isn't reported, because it left out the subagents. |
| 5 | The first command finished. **The second stopped at its 40,000 token limit, and asked a person,** just short of finishing, with its work approved by both reviewers. About a quarter of its tokens went on a writer's slips in its notes, which threw away finished work. `results_acceptance_stopped_at_limit.json` |

**This ran with the builder's own Claude Code settings:** every permission on, and this project's notes visible to the session. The session still used only the coordinating tools above. The owner has since run the quickstart by hand in a normal terminal.

**The margin is thin.** With that fixed, and the limit unchanged, run 6's second command used about 37,762 of its 40,000 estimated tokens. A few more rounds of rework would stop it. It's a limit working as designed, and you'd be asked what to do, but expect it on commands that get sent back.

The stricter reviews also came between these and the 9 commands above. Those 9 ran on the earlier playbook, so they would likely cost more now.

## Reproduce

```bash
python docs/research/journey/run_claude_code.py <output_dir> [business ...]   # Uses your Claude plan
python docs/research/journey/run_acceptance.py <output_dir>                   # Uses your Claude plan
python docs/research/journey/collect_results.py <output_dir> docs/research/journey --acceptance <acceptance_dir>
```
