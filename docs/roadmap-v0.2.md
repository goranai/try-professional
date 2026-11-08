# The plan for v0.2

A proposal, written on 2026-11-08, before anyone but the builder has used this. So **the order is a best guess, and real feedback should change it.** Nothing here is built yet.

Costs are estimates. Tokens at list price come from [the commands we measured](../README.md#what-it-costs), and on a Claude plan they come out of the plan's usage instead. The paid token figures would need the owner's approval first.

| Order | Item | Why this position | Work | Model cost |
|---|---|---|---|---|
| 1 | Three-way benchmark | It answers the question everyone will ask, and gives the next item its measure | About 1 week | About $15 to $40 at list price, on a Claude plan |
| 2 | Mixed models per role | Cheap to test, and it could cut what every command costs on a Claude plan | 2 to 3 days | About $5 to $15 at list price, on a Claude plan |
| 3 | Self-refinement for cheap models | The biggest gain in who can use this, and the biggest job | 2 to 4 weeks | About 2 to 5 million paid tokens on a cheap model |
| 4 | Claude Code plugin prototype | Small, and could cut setup steps for everyone | 1 to 2 days | Under $1 at list price |
| 5 | Hermes adapter | Real value, but for a smaller group, until demand says otherwise | About 1 week | About $2 to $5 for its journey, or free on a local model |
| 6 | Dreaming | No company is old enough to need it yet | About 1 week | About $1 to $5 at list price per test company |

## 1. Three-way benchmark: one agent, a flat swarm, and a hierarchy

**Why.** The README's FAQ has to say that no comparison has been measured. A hierarchy costs more, so people deserve to know what that buys, and whether it buys anything. It also gives self-refinement a measure to aim at.

**How.** The same tasks, about 5 of them with 3 runs each, on the three setups, with checkable criteria and a reader who doesn't know which setup wrote what. The seeded flaws from [the review test](research/qa_gates/README.md) can show how much a separate reviewer catches. **It gets published whatever it shows, including where the hierarchy loses.**

**Cost.** About 45 runs. The hierarchy's 15 are the expensive part, at $0.13 to $1.67 each. Reading the results blind takes a person a few hours.

**What would change its priority.**
- *Up, if it isn't already first:* people asking "why not just use one agent?" in Discussions or on launch posts.
- *Down:* people adopting it and asking for other things, in which case it could follow the plugin.

## 2. Mixed models per role

**Why.** [With every role on Haiku](research/haiku/README.md), nothing finished: its writers couldn't get work past its reviewers. Its reviewers did catch all 22 seeded flaws, though, and Haiku costs far less than Sonnet. A company that uses Haiku where it's enough, and Sonnet where it isn't, might cost much less without failing.

**How.** On the owner's Claude plan, through `/company`, the [acceptance journey](research/journey/README.md#acceptance-journey) and the [seeded-defect set](research/qa_gates/README.md), on three setups:

| Setup | Writers and heads | Reviewers, and light roles | Coordinating session |
|---|---|---|---|
| All Sonnet | Sonnet | Sonnet | Sonnet |
| Mixed | Sonnet | Haiku | Haiku |
| All Haiku | Haiku | Haiku | Haiku |

It compares how many commands finish, the rounds of rework, false alarms on clean documents, and tokens, split by input, cached input, and output. All Haiku has already run once, and would run again on the same commands. "Light roles" needs a definition first, such as roles whose tier is `fast`. The generated files can already set a model per role, so this is mostly measurement. If mixing works, it needs one setting to choose it.

**Cost.** Two or three days. About 3 journeys and 3 seeded runs per setup. The Sonnet runs are most of the cost, at roughly $1 to $2 a journey at list price. On a Claude plan, it comes out of usage instead.

**What would change its priority.**
- *Up, even to first:* Claude usage limits are what people hit, or cost per command is the top complaint.
- *Down:* people are happy with Sonnet's cost, or the benchmark shows a bigger problem to solve first.
- *Its own result:* if Haiku reviewers send back too much good work, Mixed could cost more than All Sonnet, from rework. That would end it, and the README would say so.

## 3. Self-refinement for cheap-model parity

**Why.** It's proven only on Claude. On `deepseek-v4.1-flash-1017`, the same small command finished one run in three, for reasons [written up here](research/journey/deepseek-diagnosis.md). A company that runs on a cheap model would cost far less, and reach people with no Claude plan.

**How.** A loop that runs a held-out set of commands, finds where a model fails, proposes changes to prompts and playbooks, and keeps only what measurably helps. It can never weaken a review, a budget, or a rule to gain a score. A held-out set, and a second model it was never tuned on, would keep it honest.

**Cost.** The largest item here. Reaching this point took about 900,000 paid tokens, so 2 to 5 million is a fair guess, and the existing 5,000,000 safeguard would stay. This is the one item whose budget should be agreed first.

**What would change its priority.**
- *Up:* people without Claude plans asking for it, `check-model` reports in bug reports, or Claude usage limits being what stops people.
- *Down:* the benchmark shows the hierarchy isn't worth its cost, which would be a bigger problem to solve first, or nearly everyone arrives through Claude Code.

## 4. Claude Code plugin prototype

**Why.** `trypro setup claude-code` already took setup from 7 steps to 5 in 0.1.2. A plugin might ship `/company` ready to use, and take a step or two more off.

**How.** The first question decides everything: can a plugin's subagent get its own server, bound to one role in one company? Role isolation depends on it. It would stop there if it can't. `pip install`, generating each company's roles, and a person's approval would probably stay.

**Cost.** One or two days, and very few tokens, because this tests setup and not work.

**What would change its priority.**
- *Up to first:* setup is the most common kind of bug report or complaint, or people give up before their first command.
- *Down:* people get through setup without trouble.

## 5. Hermes adapter

**Why.** It was an original target, and brings the company to people on another runtime. `skill-customs` also has a guide for it that has never been tried on a real install. [An issue is open](https://github.com/goranai/try-professional/issues?q=is%3Aissue+label%3Aadapter+Hermes).

**How.** An adapter on the same interface, a journey on a real install in a throwaway profile, and a plain statement of what isolation it can and can't hold. It's a good candidate for an outside contributor.

**Cost.** About a week, and little model spend, because a local or cheap model can run it. Whether it can run well on one depends on item 3.

**What would change its priority.**
- *Up:* thumbs-up on the Hermes issue, more than for any other runtime, or a contributor offering to build it, in which case it's a matter of review time.
- *Down:* another runtime gets far more requests. Then that one takes this place.

## 6. Dreaming

**Why.** A permanent company builds up memory. Consolidating it, so that a role's notes stay useful and small, should matter more as a company ages.

**How.** A command a person runs, which summarizes old memory into lessons. It uses a model, so it's never automatic, and a person decides what company memory changes, as they do now. `trypro compact` already summarizes old work logs with no model.

**Cost.** About a week, and one company with many commands behind it to test on. The second is the real obstacle, since no company has that history yet.

**What would change its priority.**
- *Up:* people with long-running companies saying that memory has gone stale, that retrieval misses things, or that token use grows with age. `trypro status` and `trypro doctor --report` could help show this.
- *Down:* nobody keeps a company long enough. Then keeping people past their first week is the bigger problem, and this waits.

## Watching for feedback

- **Bug reports:** the most common failure, from `trypro doctor --report`.
- **Discussions:** what people built in Show and tell, and what they ask in Q&A.
- **Thumbs-up** on the runtime and skill request issues.
- **Which limits people hit:** token stops, Claude usage limits, or thin skills. There's no telemetry, by design, so this only comes from what people choose to tell us.
