# What a small command costs, before and after 0.1.2

`measure.py` ran the journey's 3 small commands, on fresh copies of the journey's companies, once on 0.1.1 and once on 0.1.2. Every role is its own Claude Code process on `claude-sonnet-5-5`, with only its own server, the way the README's first cost table was measured. It used a Claude plan. Raw numbers are in `results.json`.

## Where the tokens went in 0.1.1

| Command | Model calls | Intake | Work | Reviews | Final report |
|---|---|---|---|---|---|
| Catering: two-sentence reply | 10 | 1 | 1 | 5 | 3 |
| Shorts: one title | 10 | 1 | 1 | 5 | 3 |
| Indonesian snacks: one reply | 6 | 1 | 1 | 3 | 1 |

In the first two, QA and Legal approved the work, and then Legal sent the CEO's summary of it back twice. One objection was about a character count inside the summary. The other was about the reply's own wording, which Legal had approved a step earlier, and which only the writer, and not the CEO, could change. So the summary went round again, and couldn't fix it.

## What 0.1.2 changed

A small command that one role does, whose brief is the command's own success criteria, gets its final report assembled by code from the approved work. Code adds what a person must be told: unmet criteria, open issues, and unverified claims, as it always has. So there's no CEO report to write, and no legal review of it. QA always runs, and Legal still reviews the work when its triggers apply. Large commands, and small ones a head splits into tasks, are unchanged.

**What's given up:** that second look by Legal, at the summary. In the runs above it found nothing a person needed, and when it did object, nobody in the loop could act on it.

## After

| Command | Version | Model calls | Input | Cache write | Cache read | Output | List price |
|---|---|---|---|---|---|---|---|
| Catering | 0.1.1 | 10 | 68 | 52,362 | 178,415 | 10,986 | $0.34 |
| | 0.1.2 | 4 | 30 | 20,017 | 83,794 | 3,779 | $0.13 |
| Shorts | 0.1.1 | 10 | 62 | 42,381 | 156,052 | 8,005 | $0.27 |
| | 0.1.2 | 4 | 26 | 17,033 | 67,547 | 2,811 | $0.10 |
| Indonesian snacks | 0.1.1 | 6 | 38 | 26,900 | 98,229 | 5,209 | $0.17 |
| | 0.1.2 | 6 | 36 | 25,166 | 88,716 | 4,624 | $0.16 |
| **All 3** | 0.1.1 | 26 | 168 | 121,643 | 432,696 | 24,200 | $0.77 |
| | 0.1.2 | 14 | 92 | 62,216 | 240,057 | 11,214 | $0.39 |

The Indonesian reply was sent back once for rework on its work in 0.1.2, which is why it saved little. One run of each version, so another run could differ.

## Reproduce

```bash
python docs/research/small_commands/measure.py <journey_dir> <output_dir> results.json <label>
```
