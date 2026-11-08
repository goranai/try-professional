# Three-way benchmark: not finished

**There's no result here yet, and nothing below compares the setups.** The benchmark is designed, reviewed for bias, frozen, and its harness works. It was stopped on 2026-11-08 after 9 of its 54 runs, because the full run would have used the owner's Claude plan for most of a day. It will be finished in a later version, under the same frozen design.

## What's here

- [`DESIGN.md`](DESIGN.md): the frozen rules, goals, rubrics, metrics, and how a setup wins, decided before any run. `FROZEN_SHA256` has its hash, and the harness refuses to run if it changes.
- [`bias_review.md`](bias_review.md): the bias review of the goals and rubrics, by **a separate Claude instance** (`claude-opus-5-5` through `claude -p`, with no tools and no project context), not a person or a different model family. What was done with each of its notes is at the end of the design.
- The harness: `goals.py`, `run_benchmark.py`, `grade.py`, and `report.py`.
- `partial/runs.json`: the 9 runs that completed before it was stopped, **ungraded**. They only show that every setup ran: all 9 finished. With 1 to 3 runs per setup, on different goals, and no grades, they can't say which setup does better work.
- `partial/interruptions.json`: 51 earlier runs that failed at once when the plan's 5-hour usage limit was hit, because the harness didn't recognize its wording. They were thrown away, as the design says. The harness now waits a limit out, and leaves the wait out of a run's time.

## What it would cost to finish

At list price, as Claude Code reports it, and from a Claude plan's usage: the flat swarm averaged $2.07 a run in these runs, the hierarchy $0.39, and one agent $0.06. Each swarm turn is its own Claude Code process, for every role, every round. The remaining 45 runs and the blind grading on Opus would be roughly $75.
