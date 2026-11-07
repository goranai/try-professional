# Haiku: a whole company on `claude-haiku-5-5`

Run on 2026-11-08, through Claude Code 2.1.319 on Windows 11, on a Claude plan. **On Haiku, the company held its rules, and its reviewers caught every flaw they were shown, but it finished none of its 6 commands.** That makes Haiku a poor choice for a whole company, and this doesn't show how it does in only some roles.

## How it ran

`run_haiku.py` runs everything on `haiku`: the session that coordinates, and every role's subagent, written by `trypro agents claude-code --model haiku`. It's the [acceptance journey](../journey/README.md#acceptance-journey): one prompt to a company, a website proposal for a family bakery, and homepage copy that follows it. It adds a third command, a short FAQ, with its company's limit lowered to 8,000 tokens so that it stops, which tests what 0.1.1 changed. A script stands in for the person who approves the company and answers that stop, and the record says so.

It ran twice. The first run found a bug, and the second ran on the fix.

## What 0.1.1 changed, checked on Haiku

| Check | First run | Second run |
|---|---|---|
| With no `GITHUB_TOKEN`, the person is told how to set one | Yes, before the design work | Yes, with the command, but after the design work |
| A stopped command gives the exact `trypro decide` and `/company continue <run-id>` lines | Yes, word for word | Yes, word for word |
| `/company continue <run-id>` picks up that run, and starts nothing new | **No.** It started a stray run first, then carried on with the right one | Yes |
| Every question reaches the person word for word | Yes | Yes |

**The bug, and its fix.** Haiku passed "continue <run-id>" straight to `run_start`, because the instruction to do otherwise came second. Now `run_start` picks up a run it's asked to continue, in code, and the `/company` wording says it first. The first run's harness also answered the wrong run's question, so its own checks of the stop were wrong. The table reads its record directly.

## How Haiku did

| Command | First run | Second run |
|---|---|---|
| A one-page website proposal | Stopped for a person: rework limit reached | Stopped for a person: rework limit reached |
| The homepage copy, following the proposal | Stopped: the proposal didn't exist | Stopped for a person: rework limit reached, because the proposal didn't exist |
| A short FAQ, following the homepage copy | Stopped at its lowered limit, as intended | Stopped at its lowered limit, as intended. Once continued, the rework limit was reached, because the homepage copy didn't exist |

- **Finished: 0 of 6.** The proposal never got past review. In the second run, Haiku reviewers sent it back 5 times, for real problems: source notes left in the client's text, and claims nothing supported. The Haiku writer didn't fix them, so it reached the rework limit, and stopped to ask a person. The first run stopped the same way, on whether a proposal no one could render fits on one page.
- **The later commands failed honestly.** Each one needed earlier work that was never approved. They said so and stopped, rather than invent it.
- **This isn't a 0.1.1 bug.** Since 0.1.0, nothing a role sees has changed: no prompt, no playbook, and no review logic. The only change in the pipeline is the wording of the stop message.

**Role isolation held.** All 90 artifacts across both runs were recorded as coming from the right role's own server, and none as claimed. No subagent called anything outside its own server, and the session only coordinated.

### Do Haiku's reviewers catch flaws?

The [seeded-defect test](../qa_gates/README.md) on its final set, with every role on Haiku:

| Flaw | Caught |
|---|---|
| A wrong number | 5 of 5 |
| A claim with no source | 5 of 5 |
| The wrong language | 6 of 6 |
| A requirement missed from the brief | 6 of 6 |
| **A clean document wrongly sent back** | **2 of 4** |

**Haiku's reviewers are strict, and too strict.** One sent back a clean quote because it didn't list every dish's ingredients. Legal sent back a clean script for "That's plenty if you mostly type", which Sonnet's reviewer had called minor. On Sonnet, 1 clean document in 6 was sent back.

**4 of 30 trials couldn't be counted.** Haiku's CEO called more commands large than Sonnet's did. Three became more than one task, which this test can't seed. In one, a department head assigned the task to itself, and did it in the same process, before the test could put its flawed document in. That's allowed, because it's the same role on its own server.

## Tokens

Through Claude Code, so most of the cached input is its own instructions. The price is what it reports at API list prices, which for Haiku is far below Sonnet's. These ran on a Claude plan.

| Run | Step | Input | Cache write | Cache read | Output | List price |
|---|---|---|---|---|---|---|
| Second | Design with no token | 6 | 10,811 | 21,016 | 4,326 | $0.005 |
| Second | Design the company | 42 | 62,876 | 739,633 | 10,967 | $0.025 |
| Second | Proposal | 182 | 126,526 | 1,741,997 | 37,013 | $0.054 |
| Second | Homepage copy | 200 | 115,850 | 1,850,465 | 34,608 | $0.053 |
| Second | FAQ, until it stopped | 100 | 59,968 | 855,864 | 13,965 | $0.025 |
| Second | FAQ, continued | 106 | 58,314 | 902,103 | 17,493 | $0.027 |
| | Seeded-defect test, 30 trials | 558 | 553,599 | 1,571,892 | 151,052 | $0.20 |

The proposal used about 40,000 of its 60,000 estimated tokens by the time it stopped for a person.

## What this doesn't show

- **Two runs of one journey.** A different business, or another run, could go better.
- **Haiku in some roles only.** A company with Sonnet doing the work, and Haiku reviewing or coordinating, wasn't tried.
- **Haiku with more rework allowed.** The limit of 2 rounds was kept. More might finish, at a cost.

## Reproduce

```bash
python docs/research/haiku/run_haiku.py <output_dir>    # Uses your Claude plan
python docs/research/qa_gates/run_seeded_defects.py <journey_dir> <output_dir> <results.json> retest haiku
```

`results_journey_haiku.json` is the second run, `results_journey_haiku_first_run.json` the first, and `results_seeded_haiku.json` has every verdict.
