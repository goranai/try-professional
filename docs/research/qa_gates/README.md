# Do the reviewers catch flawed work?

A seeded-defect test, run on 2026-11-08. **Reviewers caught all 24 flaws on the final playbook, in small documents, on Claude. That's less than it sounds, so read what this doesn't show.**

## How it ran

`run_seeded_defects.py` runs each trial as a real run over real MCP, on a fresh copy of a company from the [journey](../journey/README.md), so no reviewer learns from an earlier trial. The CEO's intake and every review are real: each is its own `claude -p` process with `claude-sonnet-5-5`, and only that role's server.

The one thing that isn't real is the work. When the pipeline asks for it, the script submits a prepared document through the owner's own server, reporting every criterion as met. So a reviewer gets what a careless or dishonest worker would send.

There are four kinds of flaw, one per document: a wrong number, a claim with no source, the wrong language, and a missed requirement. A clean version of each document goes through too, because a reviewer that rejected everything would otherwise catch every flaw.

A flaw counts as caught only if a reviewer rejected the work with a major or blocking issue about that flaw. Rejecting it for something else doesn't count. A script checks this by keywords, and the person building this also read every verdict. `scenarios.py` has every document.

## Results

| Flaw | Round 1: easy documents | Round 2: harder documents | Round 3: harder and new documents, stricter playbook |
|---|---|---|---|
| Wrong number | 5 of 5 | 4 of 4 | **6 of 6** |
| Claim with no source | 5 of 5 | **3 of 4** | **6 of 6** |
| Wrong language | 5 of 5 | 4 of 4 | **6 of 6** |
| Missed requirement | 5 of 5 | 4 of 4 | **6 of 6** |
| Clean document rejected, a false alarm | 0 of 5 | 1 of 4 | **1 of 6** |

Every rejection in all three rounds was recorded as coming from the reviewer's own server, and its words reached the worker's next assignment.

- **Round 1 was too easy.** Documents were short, commands stated most of the answers, and every worker's evidence read "see outputs". A perfect score there says little.
- **Round 2 made the reviewer do the work.** Totals weren't in the command. A wrong number was carried through, so the sums still added up. Claims had no figure to catch the eye. One paragraph was in the wrong language. A limit needed counting. And the worker said every time that it had checked everything line by line.
- **Round 2 found a real miss.** A reviewer noted "Most people never fill even one notebook" as minor. It sent the script back for another reason, but on its own that invented claim would have been approved.
- **The fix.** The review playbook now says to check the work rather than rely on its evidence: redo every sum, compare each fact with the brief, read all of it for language, and count any limit. An unsupported claim of fact is at least major. This only makes reviews stricter, and the prompt budget wasn't raised.
- **Round 3 retested that.** It used round 2's documents and two new scenarios written after the change, 8 of its 24 flaws, so those couldn't have shaped it.

### The false alarms

Round 2's was partly the test's fault. The "clean" script ended with "Tell me in the comments", when the command asked it to end with a question. That document was corrected before round 3, so `scenarios.py` differs from round 2 by that one line.

Round 3's is a cost of the stricter rule. A reviewer sent back a clean quote because it never said outright that no dish contains pork or shellfish, and because the menu dishes weren't in the brief, even though the command left the menu to the worker. **Expect some sent-back work that didn't need it.**

## What this doesn't show

- **Only one model reviewed.** All of this is `claude-sonnet-5-5`. A cheap model hasn't been tried, and this says nothing about one.
- **Small numbers.** 24 of 24 is one run of each document. If reviewers really missed 1 flaw in 10, a run like this would still come out perfect about 8% of the time.
- **Small documents, one flaw each.** They're about 20 to 180 words. Long documents, several flaws at once, and large commands with many tasks weren't tested.
- **The facts were all in the command.** A reviewer could always check against it. A wrong fact about the world, which needs outside knowledge, wasn't tested. The company has no web access to check one.
- **Legal wasn't tested on its own.** QA caught every flaw first, and a trial ends at the first rejection, so Legal only ever read clean documents.
- **Fixing wasn't tested.** A trial stops once work is sent back. The [acceptance journey](../journey/README.md#acceptance-journey) has a case of a real worker fixing what a reviewer found, and that's one case.
- **Not independent.** The person building this wrote the documents, chose the flaws, and read the verdicts. Reviewers also raised other complaints beside the seeded flaw, and some weren't fair. One counted 96 words in a 106-word script: it rejected the script rightly, with the wrong count.

## Tokens

Intake and reviews only, since the work is a prepared document. Through Claude Code, so most of the cached input is its own instructions. The price is what it reports at API list prices. These ran on a Claude plan.

| Round | Trials | Input | Cache write | Cache read | Output | List price |
|---|---|---|---|---|---|---|
| 1 | 25 | 334 | 217,361 | 806,301 | 35,697 | $1.31 |
| 2 | 20 | 264 | 190,643 | 655,122 | 37,617 | $1.20 |
| 3 | 30 | 394 | 285,157 | 988,288 | 54,707 | $1.79 |

## Reproduce

It uses your Claude plan, and needs companies from the journey.

```bash
python docs/research/journey/run_claude_code.py <journey_dir>
python docs/research/qa_gates/run_seeded_defects.py <journey_dir> <output_dir> <results.json> first
python docs/research/qa_gates/run_seeded_defects.py <journey_dir> <output_dir> <results.json> retest
```

Raw verdicts are in `results_round1.json`, `results_harder.json`, and `results_retest.json`.
