# How soon a stuck run stops

An external retest of 0.1.2, on the API runner and `deepseek-v4.1-flash-1017`, ran a small command that never finished. It stopped at its 60,000 limit. The tester resumed it with a limit of 250,000, and it stopped again, as stuck, only at 257,476 tokens and 48 model calls. Its writer had used 12 calls on one step with nothing accepted.

## Why it took so long

Stuck detection counts model calls since anything moved. In 0.1.2 that included a manager's answer to an escalation, and any new assignment. In this run, the writer's work was sent back by QA three times, which escalated to its lead, and the lead's answer and the writer's new assignment each reset the count. So the writer's last 13 calls looked like a fresh start, and the run stopped only when it reached its new limit.

## What 0.1.3 changed

- **What counts as progress:** work accepted, approved, or sent back for rework, or a task added or settled. Not an answer to an escalation, and not the same work assigned again.
- **Two more ways to count as stuck,** besides 24 calls with nothing moving:
  - 3 hand-ins refused, as malformed or incomplete, with nothing moving.
  - **A whole command's limit in tokens with nothing moving:** 60,000 for a small command on the API runner, for example. Budget a person adds doesn't raise it, so resuming a loop doesn't buy more of it.

**Why a whole limit, and not less.** In this same run, one real step on this model, which did hand in valid work in the end, cost 63,887 tokens over 13 calls. Half a limit would have stopped steps like that, and a "slow but fine" writer in the scenarios below, every time.

## Measured

`measure.py` plays scenarios on the scripted model, with each call reporting the tokens the real run's calls reported. When a run asks about its budget, it resumes with a limit of 250,000, as the tester did. It stops at the first other question. `results.json` has every question each run asked.

| Scenario | 0.1.2 | 0.1.3 |
|---|---|---|
| **The external run, replayed call by call** | stuck at 257,476 tokens, 48 calls | stuck at 85,516 tokens, 19 calls |
| A writer that never hands anything in | stuck at 64,701, 11 calls | the same |
| A writer whose hand-ins are all refused | stuck at 64,701, 11 calls | stuck at 48,008, 8 calls |
| Work sent back by QA three times, then a loop | stuck at 252,035, 37 calls | stuck at 230,125, 34 calls |
| A slow writer that hands in valid work after 6 calls | done, 39,172 | done, 39,172 |

**The replay** follows the real run's audit log: its token counts, and what each call did. It has no content. 0.1.2 reproduces the real run's numbers exactly. 0.1.3 stops it at 85,516 tokens, a third of the tokens and 40% of the calls, and after the resume it spends 25,000 tokens rather than 197,000.

**It stops on the call where the writer's second, slow attempt was handing in.** That attempt was sent back by QA in the real run, and the run never finished, so stopping there cost nothing. But it shows a slow step on a cheap model can be called stuck. If a person chooses to retry, the work handed in on that call still goes to review.

**Work sent back three times, then a loop, saves little.** Most of its tokens are real attempts that QA sent back, and a rework round is progress: it's how the hierarchy works. The escalation to the lead after the third one is the hierarchy's own answer. Only the loop after it is cut short.

## What it doesn't show

- **The scripted model, not a real one.** The replay repeats what one real run did. A real model given another path could do something else.
- **One run, on one model.** Steps on other models cost other amounts, and Sonnet's steps were much cheaper in the journeys.

## Reproduce

```bash
python docs/research/stuck/measure.py <label> results.json
```

It measures whichever version of `try-professional` is installed.
