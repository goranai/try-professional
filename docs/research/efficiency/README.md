# Efficiency: 0.6.1 and 0.6.2, on the same real jobs

The same seven commands, on `step-5-preview`, through the company's own engine, with the shell and the web on and nobody there to approve anything outside. 0.6.1 was installed from PyPI; 0.6.2 ran from the repository. Every run was measured afterwards from its own audit log, by `try_professional.core.efficiency`, so both versions are measured the same way. Minutes are minutes of work: time waiting for a person is left out.

`measure.py` reproduces it. `results.json` has every run, including the ones left out of the table below, with calls and tokens by step and by role.

## Result

| Job | 0.6.1 | 0.6.2 |
|---|---|---|
| A two-sentence customer reply | done, 0.7 min, 6 calls, 22k tokens | done, **0.3 min, 3 calls, 8k** |
| A week's Instagram plan, five posts | done, 4.6 min, 24 calls, 177k | done, **1.8 min, 10 calls, 47k** |
| Fix one sentence on a menu | done, 0.9 min, 9 calls, 36k | done, **0.5 min, 6 calls, 21k** |
| Find a GitHub repository with `gh`, and report on it | done, 1.3 min, 12 calls, 53k | done, **0.7 min, 9 calls, 34k** |
| A two-sentence reply, in Indonesian | done, 1.1 min, 7 calls, 28k | done, **0.8 min, 6 calls, 23k** |
| Make content for a new product, ready to post: research, a post, a posting time | **blocked**, 3.0 min | **done**, 8.0 min, 57 calls, 588k |
| Find a real mistake in a popular repository's README, check it, and prepare an issue | done, **2.8 min, 19 calls, 167k** | done, 11.4 min, 35 calls, 367k |

**The five small jobs:** 8.6 minutes, 58 calls, and 316k tokens in all on 0.6.1; 4.1 minutes, 34 calls, and 133k on 0.6.2. Faster on every one, with 18% to 73% fewer tokens. The CEO now sends work to the fewest roles that can do it.

**The large jobs are mixed.** The content job finished on 0.6.2 and was blocked on 0.6.1. The repository job was slower: 5 of its 11.4 minutes were one `gh search` that hung until the 5-minute limit stopped it, and without that it took about 6 minutes, still more than 0.6.1's 2.8.

## Read this before quoting it

- **One run per job, per version.** Runs of the same job vary a lot. An earlier 0.6.2 build did the content job in 2.8 minutes, and the final one took 8.0; the same build did the repository job in 19.6 minutes before the fixes below. Treat the small jobs' direction as solid, and the large jobs' numbers as one sample each.
- **One model.** Only `step-5-preview` was measured.
- **The company's own engine only.** In Claude Code, 0.6.2 also starts ready roles at the same time; that's checked separately, on Haiku, below.

## What the measuring found, and what changed because of it

Each of these was found by a run above, and fixed before the final round:

- A step's conversation grew with every page a role read: an analyst sent 2.8 million input tokens in 22 calls, and the run ended in a gateway error. A step now keeps its 6 newest tool outputs whole and cuts older ones, in batches.
- A worker verified everything twice, and its reviewer re-ran every search: 73 calls on one job. Workers stop when the definition of done is met, and reviewers of computer work re-run only what their verdict rests on, within 16 turns.
- A shell command that started a program hung a run for 90 minutes. A time limit now stops everything the command started.
- Hand-ins refused over wording: a payload sent as JSON text, a criterion's "met" in other words, a task inside a `brief`, a role named for its department, flags inside the payload, and confidence as "moderate" or 0.8. Each is understood now. Whether a criterion was met is never guessed: with no "met" at all, it's still refused.

## 0.6.3 on Haiku, through Claude Code: 21 runs, 21 finished

The same seven jobs, three times each, the way a person uses it: `/company <job>` in Claude Code, with the session and every role on `claude-haiku-5-5`, one run at a time. Roles could use the shell and the web without asking, and the company's deny rules stopped anything that changes something outside, so nothing was posted. Time, cost, and tokens are Claude Code's own account of each whole session, subagents included; cost is its list-price figure. `measure_claude_code.py` reproduces it, and `results_haiku.json` has every run, including the two earlier rounds below.

| Job | Finished | Minutes, average (range) | Cost, USD, average (range) | Fresh tokens, thousands | Read from cache, thousands |
|---|---|---|---|---|---|
| A two-sentence customer reply | 3 of 3 | 1.3 (1.3 to 1.3) | 0.014 (0.014 to 0.015) | 48 | 494 |
| A week's Instagram plan, five posts | 3 of 3 | 2.4 (1.6 to 3.2) | 0.023 (0.016 to 0.028) | 73 | 701 |
| Fix one sentence on a menu | 3 of 3 | 1.4 (1.3 to 1.5) | 0.014 (0.013 to 0.015) | 45 | 513 |
| Find a GitHub repository with `gh`, and report on it | 3 of 3 | 1.6 (1.5 to 1.6) | 0.015 (0.015 to 0.016) | 50 | 565 |
| A two-sentence reply, in Indonesian | 3 of 3 | 1.5 (1.4 to 1.5) | 0.015 (0.015 to 0.016) | 50 | 514 |
| Make content for a new product, ready to post: research, a post, a posting time | 3 of 3 | 5.2 (3.2 to 8.1) | 0.108 (0.077 to 0.146) | 315 | 1,667 |
| Find a real mistake in a popular repository's README, check it, and prepare an issue | 3 of 3 | 2.6 (2.1 to 3.2) | 0.025 (0.020 to 0.031) | 77 | 864 |

Minutes are wall-clock, from start to the answer, including setup. The content job, the only one that uses three departments and the web, varies the most.

**How it got here.** Two earlier rounds of the same jobs, on earlier builds, are in `results_haiku.json`. The first finished 16 of 19 runs, and the second 19 of 21. Every run that didn't finish was read, and its cause fixed before the next round:

- Reviewers sent back [placeholders] and phrases like "made with care" as unsourced facts, while asking for captions written out in full: no answer could pass. Now only specific, checkable claims need a source or a placeholder, and a placeholder for a fact the brief didn't give is the right answer.
- Each review of reworked work started over and found a new list of issues, round after round, until the rework limit. A reviewer now sees its own earlier verdict, lists everything the first time, and checks that list after.
- A check a workspace can't do, such as how a page prints, was sent back again and again. It now passes when marked unverified for a person.
- Haiku restated criteria in its own words, added fields of its own, wrapped an answer in another layer, and mistyped a 24-character id six times. Each is understood or filled in by code now.

**Read this before quoting it.** Three runs per job, one model, one machine. Nothing was posted, so these runs measure the work up to the point a person approves it.

## Roles at the same time, in Claude Code

`/company` with every role on `claude-haiku-5-5`, and Haiku for the session, on a command with two independent jobs for two departments. On the first try, Haiku started each subagent in its own message: the two writers and the two reviewers overlapped, and the two department heads worked one after the other. `run_next_action` now tells the coordinator to start ready roles together when there are several.

Then again, with that change, on another two-department command. Haiku still started each subagent in its own message, but now in the background, right away, so the steps ran together: the two heads started 4 seconds apart, the writers both worked from 06:45:27 to 06:45:36–37, and the reviewers from 06:45:49–50 to 06:45:54–55. The command finished, through every review, in 1.8 minutes and 22 model calls, with nothing posted.
