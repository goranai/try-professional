# Mixed models per role

**Written on 2026-11-08, before any of its runs.** It reuses the [three-way benchmark](../benchmark/DESIGN.md)'s goals, companies, budgets, scripted person, closing instruction, blind grading, and rubrics, unchanged. Only the models differ.

## The question

[With every role on Haiku](../haiku/README.md), nothing finished, but its reviewers caught every seeded flaw. Does a company cost less with Haiku where it's enough, and Sonnet where it isn't, without doing worse work?

## The setups

All three are the hierarchy, run exactly as the benchmark runs it, with `/company` in Claude Code.

| Setup | Roles that write, and heads | Reviewers | The coordinating session |
|---|---|---|---|
| All Sonnet | Sonnet | Sonnet | Sonnet |
| Mixed | Sonnet | Haiku | Haiku |
| All Haiku | Haiku | Haiku | Haiku |

**A reviewer** is a role in a department named for review (QA, legal, compliance), or whose id or title says reviewer, QA, compliance, or counsel. Every other role writes or leads. The rule is decided here, and applied by code to each company as designed.

**All Sonnet is the benchmark's own hierarchy runs,** 3 per goal. Mixed and All Haiku get 1 run per goal each, 12 runs in all, after the benchmark, under the same budget caps.

## What's compared

Pass rate, must items met over all runs, finish rate, holistic score, and dollars at list price per run and per passing run. The grader doesn't know which setup made the work, and grades these runs with the benchmark's.

## How it's decided

Decided now:
- **Mixed is worth recommending** only if it passes as many goals as All Sonnet, and costs less per passing run.
- Otherwise it stays an option, `trypro agents claude-code`'s default by tier, and the README says it wasn't shown to save money without losing quality.
- With 1 run per goal, this can show a large difference, and can't show a small one.
