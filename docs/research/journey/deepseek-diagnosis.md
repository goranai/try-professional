# Why `deepseek-v4.1-flash-1017` blocked, for v0.2

Written from saved logs on 2026-11-07, with no new model calls. It explains what the logs can show, and says where they can't. It isn't a verdict on the model's quality: nothing here measures that.

## What happened

`check-model` said "suitable", and then the same model didn't finish one small command, twice. The command was "Write a two-sentence welcome note for a new newsletter subscriber."

| Run | Result | Input (of which cached) | Output | Total | Rejected outputs | Rework rounds | Escalations |
|---|---|---|---|---|---|---|---|
| First | Stopped at the old 200,000 limit | 176,240 (120,064) | 35,776 | 212,016 | 14 | 4 | 2 |
| Second | Legal blocked the final report | 38,876 (20,992) | 8,406 | 47,282 | 2 | 1 | 1 |

Each total includes the rule checks, which cost about 3,700 tokens on the first run (208,291 at the stop, against 212,016 in all). Both commands were sized `small`.

## What the logs can and can't show

**The first run can't be fully diagnosed.** The script didn't save why outputs were rejected, and the run's folder was temporary, so only its counters remain: 14 rejected outputs across 11 assignments, and 4 rounds of rework. That's enough to say it spent its tokens on repetition, and not enough to say on which step. Anything more specific would be a guess. The script now saves rejections and review issues.

**The second run is clear:**

1. The writer produced an unbranded note, and said so honestly.
2. Legal raised sender identity and consent. In rework, the writer added a `[BRAND NAME]` placeholder and an unsubscribe statement.
3. QA and legal passed the work, and then legal, reviewing the final report, blocked it: the sender is a placeholder (CAN-SPAM §7704(a)(1)), there's no postal address (§7704(a)(5)), and the consent basis isn't evidenced (GDPR Art. 6, 7).
4. The CEO passed that to a person, as the rules require, and the command ended blocked.

Two output rejections were the model adding fields that don't exist: `constraints_note` and `unverified_scope`. The same kind of slip appeared in the earlier generator evaluation.

**Older journey runs on this model say little about it.** Three Shorts commands blocked in the first journey, for 218,923 tokens including the company's design. Those runs predate fixes to bugs of ours, which they helped find: a role named without its department, plans asking for web sources the company couldn't reach, and no memory of earlier work. Don't count them against the model. They do show where tokens go in a stuck run: execution and escalation, not planning. "Make 3 more Shorts" spent 38,877 on execution, 36,312 on dispatch and 30,676 on escalation, against 8,896 on planning.

## Causes, most certain first

1. **The rule checks didn't predict finishing work.** That was a flaw in `check-model`, now fixed: "suitable" requires finishing a real command within 40,000 tokens.
2. **A person's question came too late, and with nothing to show.** Legal's objection was fair for an email going out: only a person knows the sender and the consent basis. The command still ended with no draft in the person's hands. This is partly the product, not the model.
3. **The final review can't open the underlying work.** The reviewer wrote that two referenced artifacts "could not be opened" and that it reviewed only text pasted into the brief. That's a product gap, and it holds for any model.
4. **This model may treat a missing context as a blocker more readily.** It's plausible, and unproven. A catering company's small command passed legal on Claude Sonnet, but it was a different command in a different company, so this isn't a fair comparison.
5. **Rejections are expensive.** Each one resends the whole assignment. How much of the first run's cost this explains is unknown.

## What the fixes so far change

A small command now stops at 40,000 tokens and a large one at 150,000, by default, and reports what happened. Both runs above would have stopped at about 40,000 tokens. The stop limits the damage, and it doesn't make the model finish.

## For v0.2

None of these may weaken QA, legal review, or any other gate.

- **Deliver the draft along with the question.** When legal blocks work because only a person has the facts, give the person the draft, clearly marked as not cleared, and the list of what's needed.
- **Show a final reviewer the work it's reviewing,** within a token limit, so that it isn't judging a summary.
- **Measure before tuning.** Run the same commands on this model and on Claude, several times each, in the same company, and save every rejection and verdict. One or two runs can't separate the model from the noise.
- **Look at the cost of a rejection.** See whether a rejected output can be corrected in the same conversation for less, given how often models add fields.
- **Self-refinement** then has a real target: the rate at which a cheap model finishes small commands within their limit, with quality judged separately.
