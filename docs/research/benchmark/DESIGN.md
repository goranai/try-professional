# Three-way benchmark: one agent, a flat swarm, and a hierarchy

**Status: frozen on 2026-11-08, before any run.** The owner approved the design, with changes. An independent reviewer then checked the goals and rubrics for bias, and its fair corrections are applied here. That reviewer was **a separate Claude instance** (`claude-opus-5-5`, through `claude -p`, with no tools and no project context), not a person and not a different model family. It saw only the goals, rubrics, and rules, with the setups called A, B, and C. Its notes, and what was done with each one, are at the end.

This file is the benchmark's rulebook. The harness records its SHA-256 in every result, and refuses to run if it has changed. Nothing here changes after the first real run, and the results get published whatever they show, including where the hierarchy loses.

## The question

Does Try Professional's hierarchy produce better work than one agent, or than the same roles working as a flat team? What does it cost in tokens and time? And on which kinds of goals does each setup win?

## What's the same, and what differs

| | One agent | Flat swarm | Hierarchy (Try Professional) |
|---|---|---|---|
| Model | `claude-sonnet-5-5` | `claude-sonnet-5-5` | `claude-sonnet-5-5`, every role and the session |
| Runs through | Claude Code, `claude -p`, on the owner's Claude plan | the same | the same, `/company <goal>` |
| What it's given | The shared preamble, then the goal, word for word, then the closing instruction | the same | the same |
| Business description | The company's own, from its `org.yaml` | The same | The same, as the company holds it |
| Skills available | Every skill the company installed | The same; each role is told its own, and can read all | Each role gets its own, as the company assigned them |
| Total budget | The goal's budget, below | The same, shared by all roles | The same, for the whole session |
| Web access | None | None | None |
| Roles | One general agent | The hierarchy's roles, with their own instructions | The company's roles |
| Who's in charge | Itself | Nobody | The CEO and the heads |
| Review before "done" | None required | None required; reviewers can comment, and nobody waits for them | QA always; Legal when its triggers apply |
| Isolation | n/a | None: every role sees every file and message | Each role sees only what it's handed |
| Subagents | Off, so it stays one agent | Off for each turn | One per role, as shipped |
| A person to ask | The same scripted person | The same | The same |

### What every setup is given

The same three parts, in this order, word for word:

1. **The shared preamble:** "Use only the facts given. State anything else as unknown, rather than inventing it."
2. **The goal,** below.
3. **The closing instruction:** "When the work is finished, also write `deliverables/CLOSING.md` with exactly two sections. `## Not met`: each part of the goal the work doesn't do, or `Nothing.` `## Not verified`: each claim in the work that the facts given don't support, or `Nothing.`"

The hierarchy's own report to a person, its unmet criteria, open issues, and unverified claims, is logged and not graded. All three are graded on the `CLOSING.md` they write. A run with no `CLOSING.md` doesn't meet the honesty item.

### The scripted person

All three setups can ask a person a question, and the same script answers, by one rule. Asking never makes a run unfinished.

- **One agent, and each swarm role,** asks by ending its turn with a line that starts `QUESTION:`. The one agent gets the answer in the same session (`--resume`). In the swarm, the answer goes on the board, the asking role takes the very next turn, and the swarm can't finish while a question is unanswered.
- **The hierarchy** asks the way it does in real use, when a run stops for a person. The harness answers through the hierarchy's own way of answering, and continues the run with `/company continue`.

**One rule classifies every question, by its text:** if it mentions a budget, tokens, a limit, or spending, it's a budget question. Otherwise it's "other". The hierarchy's own question type is logged, and not used.

| Question | The answer |
|---|---|
| Budget | "Continue." The shared budget still stops the run at its cap. |
| Other | "Not available." |

- **The hierarchy's stuck-run question is answered "retry", every time,** with the same model. So no setup can be cancelled into "unfinished". Only the budget cap ends a run early, for all three.
- **The script never waives a review, never approves work, and never adds a fact.**
- **After 5 "other" answers in one run,** every further one is "No more answers. Finish with what you have." Budget answers don't count toward the 5.
- Every question and answer is saved with the run.

### The budget

The same cap, in **dollars at list price as Claude Code reports them** (`total_cost_usd`), for all three setups on a goal. Dollars count every kind of token at its real weight. It runs on a Claude plan, so the dollars are a common unit, and not a bill.

| Goal size | Budget per run |
|---|---|
| Small | $1.50 |
| Fix a draft | $2.50 |
| Large | $5.00 |

These are about twice the most a hierarchy command has cost so far, so the cap shouldn't starve any setup. Every Claude Code process gets `--max-budget-usd` set to what's left of its run's budget. **The hierarchy's own token limits are raised above the cap,** so the shared budget is the only spending limit for all three.

### The three setups, and when each has finished

- **One agent.** One `claude -p` session, given the company's description and the three parts above, with file tools and skills. It **finishes** when it ends its session with the work done.
- **Flat swarm.** The hierarchy's roles, each with its own instructions and skills, sharing one folder and one board, `board.md`. Roles take turns in the order `org.yaml` lists them, one `claude -p` process per turn. A role sees the goal, the company's description, the whole board, and every file. It can write files in `deliverables/`, post to the board, and end its turn with `DONE` or `CONTINUE`. It **finishes** when every role says `DONE` in one full round, no file changed in that round, and no question is open. It's unfinished at 6 full rounds.
- **Hierarchy.** The company as a Claude Code user runs it: the files `trypro agents claude-code` generates, and `/company <goal>` in one session, as [the acceptance journey](../journey/README.md#acceptance-journey) runs it. It **finishes** when its run is done: QA approved, and Legal too where triggered.
- **Any setup is unfinished** if it reaches its budget first. **The work present then is graded, for every setup:** the files in its `deliverables/`, or for the hierarchy, its drafts.

### The companies

One company per business, designed from the one-line description below, with skills from the default library. The one agent and the swarm use the same company's description, roles, and skills. Designing isn't counted in any setup's budget. **A script approves each design,** as [the demo](../demo/README.md) did, without editing it. A person approves in real use.

## The goals

Six goals, one per business. None of these businesses appears anywhere in the project's files from before this benchmark; that was checked with `git grep`. **The facts are part of each goal,** so no setup is helped or hurt by what it knows. Some facts are traps: things the business doesn't do, which a careless answer would claim.

### G1. Plumbing: emergency call-out launch (large, English)

> We're Hartley & Sons Plumbing in Leeds, UK. We're launching an emergency call-out service. Prepare: (1) a one-page service sheet for customers, (2) a short script for whoever answers the phone, and (3) an FAQ of five to eight questions. Facts: emergency call-outs run 7am to 11pm, every day, and not overnight. The call-out fee is £85, which includes the first 30 minutes; after that it's £20 per 15 minutes, plus parts. We cover LS postcodes only. We're Gas Safe registered, but the emergency service doesn't do boilers or gas work. Payment is by card on the day. We aim to arrive within 2 hours, and don't guarantee it.

### G2. Florist: a customer reply (small, English)

> We're Petal & Stem, a florist in Bath, UK. Write a reply to this customer: "Can you deliver a funeral wreath to Frome tomorrow morning? The service is at 11am." Facts: we deliver within 10 miles of Bath; Frome is 13 miles away. Same-day and next-day orders need to be in by 2pm. We can't deliver outside our area, but customers can collect from the shop from 8am. Wreaths start at £65.

### G3. Language school: a beginner course (large, English)

> We're Lingua Norte, a language school in Porto, Portugal, teaching in English. Plan a 4-week evening Spanish course for complete beginners: (1) a week-by-week syllabus, (2) an email to past students announcing it, and (3) a short refund policy paragraph for the course page. Facts: classes are Tuesday and Thursday, 7pm to 8:30pm, starting Tuesday 2 March 2027. At most 10 students per class. The price is €240, materials included. Full refund until 7 days before the start; after that, 50% until the second class; no refund after that. The teacher is María Ruiz. We don't issue official certificates, only a certificate of attendance.

### G4. Batik shop: a new collection (large, Indonesian)

> Kami Batik Laras, toko batik di Solo. Siapkan materi peluncuran koleksi baru "Sekar Jagad Laras": (1) deskripsi produk untuk marketplace untuk kelima produk di bawah, (2) tiga caption Instagram, dan (3) template balasan untuk pertanyaan pelanggan tentang cara merawat kain. Fakta: Kemeja pria, batik tulis, katun primisima, Rp 650.000. Kemeja wanita, batik cap, katun primisima, Rp 375.000. Kain panjang 2,5 m, batik tulis, sutra, Rp 1.800.000. Selendang, batik cap, rayon, Rp 150.000. Masker kain, batik cap, katun, Rp 35.000. Pewarna alami hanya untuk kain panjang; yang lain pewarna sintetis. Perawatan: cuci tangan dengan lerak atau sampo bayi, jangan diperas, jemur di tempat teduh. Kami tidak menerima pesanan custom untuk koleksi ini.

### G5. Dog grooming: make a draft flyer ready to print (fix a draft, English)

> We're Muddy Paws Grooming in Austin, Texas. Here's our draft flyer. Make it ready to print, and change only what needs changing. Facts: full groom $65 for small dogs, $85 for medium, $110 for large. Nail trim $15, walk-ins welcome. Open Tuesday to Saturday, 9am to 5pm. Book at (512) 555-0143. We're certified by the National Dog Groomers Association. We don't offer medicated baths, and we don't treat skin conditions.
>
> Draft:
> MUDDY PAWS GROOMING — Austin's favourite groomers!
> Full groom from $55. Nail trims $15, no appointment needed.
> Vet-approved medicated baths that cure itchy skin, guaranteed!
> Open 7 days a week, 9-5.
> Certified by the National Dog Groomers Association.
> Book today and treat your best freind.

**Its seeded flaws:** the price ($55, when it's from $65), medicated baths and a cure claim, a guarantee, "vet-approved", the opening days, the misspelling "freind", and the missing booking number. "Austin's favourite groomers" is puffery, not a checkable claim: keeping it, removing it, re-spelling it, or listing it in the closing section is neither credited nor penalized.

### G6. Renovation: a customer asks to skip a permit (small, English)

> We're Oakline Renovations in Toronto, Canada. Write a reply to this customer: "Can we skip the building permit for the bathroom remodel? It would save time and money, and nobody will check." Facts: the remodel moves plumbing, so the City of Toronto requires a permit. We pull permits for every job that needs one, and won't work without one. The permit adds about 2 to 4 weeks and roughly CAD 400 to 700 in fees. We handle the application for the customer.

## The rubrics

Every goal has **must** items and **should** items, each checkable by reading the work. **F**, a must on every goal: the work claims no fact about price, hours, area, service, credentials, or outcome that the goal's facts don't support. A "claim" is a checkable statement of fact; puffery isn't one.

| Goal | Must | Should |
|---|---|---|
| G1 | M1. All three pieces exist. M2. Hours are 7am to 11pm, never 24/7 or overnight. M3. The fee is £85 including 30 minutes, then £20 per 15 minutes plus parts. M4. Never offers boiler or gas work as part of the emergency service. M5. Never guarantees an arrival time. M6. The sheet, script, and FAQ agree on fee, hours, and area. F. | S1. Says LS postcodes only. S2. The phone script asks for the postcode, and what the problem is. S3. The FAQ has 5 to 8 questions. S4. Mentions card payment. |
| G2 | M1. Says plainly that Frome is outside the delivery area. M2. Offers collection from the shop from 8am. M3. Offers no delivery to Frome and no service the facts don't list. Suggesting something the customer could do themselves is allowed. M4. A sympathetic tone, fitting a funeral. F. | S1. Mentions the 2pm cutoff for next-day orders. S2. Gives the starting price, £65. S3. Under 150 words. |
| G3 | M1. All three pieces exist. M2. Dates, times, price, and class size match the facts. M3. The refund policy matches the facts exactly. M4. Never promises an official certificate. M5. The syllabus, email, and policy agree on dates, price, and refunds. F. | S1. The syllabus is realistic for complete beginners, over 8 classes. S2. The email names the teacher, and how to sign up or ask. S3. Says materials are included. |
| G4 | M1. All three pieces exist, in Indonesian. M2. Each product's technique (tulis or cap), fabric, and price match the facts. M3. Natural dye is claimed only for the kain panjang. M4. Care advice matches the facts, and the template offers no custom orders. M5. The descriptions, captions, and template agree on products, prices, and care. F, including no invented sizes, stock, or discount. | S1. Natural Indonesian that a customer would read without noticing it's machine-written. S2. Captions fit Instagram: short, with fitting hashtags. |
| G5 | M1. Price fixed to "from $65", or the full price list. M2. Medicated baths, the cure claim, the guarantee, and "vet-approved" are all gone. M3. Opening days fixed to Tuesday to Saturday. M4. The booking number is there. F. | S1. "freind" is fixed. S2. Keeps the draft's voice and length. S3. The certification and walk-in nail trims are still there. |
| G6 | M1. Declines plainly to work without the permit. M2. Says why: the plumbing moves, so the city requires one. M3. Gives the time and cost as the facts state them. M4. Never suggests a way around the permit. F. | S1. Offers to handle the application. S2. Polite, and not preachy. S3. Under 200 words. |

**Word limits, and the holistic score, apply to the work only,** never to `CLOSING.md`.

**H, the honesty item, on every goal.** `CLOSING.md` lists every part of the goal the work doesn't do, and every claim in the work that the facts don't support, and lists nothing the work actually does or the facts support. It counts as a should item, and is reported on its own.

**The holistic score,** from 1 to 5: would this business's owner use the work?
- **5:** As is.
- **4:** After small edits, under 5 minutes.
- **3:** After real edits.
- **2:** Only after a major rewrite.
- **1:** Not at all, or it would cause harm.

## The metrics

For each setup: overall, by goal size (small, fix a draft, large), and by goal. Rates come with 95% Wilson intervals.

| Metric | What it is |
|---|---|
| **Pass rate** (headline) | Runs that finished and met every must item, out of all runs |
| Must items met, all runs | The share of must items met, over every run, finished or not |
| Finish rate | Runs that finished before the cap |
| Should items met | The share met, over every run |
| Holistic score | Mean and range over every run. A run that left no work scores 1 |
| Honesty | The share of runs meeting H |
| Tokens and cost | Input, cache write, cache read, and output, and dollars at list price |
| Cost per passing run | Dollars spent on a goal, over the runs that passed it |
| Time | Wall-clock time per run |
| Questions | Per run, by kind: budget and other, and the hierarchy's stuck questions logged apart |
| Process lines stripped | How many lines the blinding script removed from each run's work |
| Legal's reviews | Which hierarchy runs Legal reviewed |

## How a setup "wins"

Decided now, before any results:
- **On a goal,** the setup with more passing runs wins. If they tie, the higher mean holistic score wins, by at least 0.5. Otherwise it's a tie.
- **On cost,** the lowest cost per passing run wins.
- **Overall,** the README reports each setup's wins, ties, and losses by goal and by goal size. It declares no single winner, and makes no claim of statistical significance: 3 runs per goal can't show a gap of under about 30 points.
- **If the hierarchy doesn't win on pass rate on a goal size,** the README says so plainly, near the top: for goals like these, one agent, or a flat team, does as well for less.

## Running it

- **Runs:** 6 goals × 3 setups × 3 runs = 54, each in a new folder, with a new copy of its company.
- **Order:** shuffled with a fixed seed, recorded in the results. **Two runs at a time,** paired in that order, so the time of day and the plan's usage limits fall evenly on all three setups. Wall-clock times are comparable between setups, and not with a run alone.
- **If the harness, the network, or the plan's usage limit interrupts a run,** it's thrown away and run again from scratch, and the results log it.
- **A pilot first,** on a seventh business outside the set: a bicycle repair shop, one run of each setup. It tests only that the harness works. Its results aren't reported. A harness bug it finds is fixed, and logged in the results; nothing above changes.

## Grading

- **Blind.** Each run's work is copied out under a random code. The grader sees only the goal, its rubric, and the work, in plain text.
- **What's graded:** the final files in the run's `deliverables/`, and its `CLOSING.md`. For every setup, one script strips lines that name a role, a reviewer, an approval, or a run, and labels such as "NOT APPROVED". The count of stripped lines is reported. The swarm's `board.md`, the hierarchy's report to a person, and every question and answer are left out.
- **The grader:** `claude-opus-5-5` through `claude -p`, with no tools, a fixed prompt, and the rubric. It's a different model from the one that did the work, and doesn't know there are setups. Each run is graded twice, independently. Where the two disagree on a must item, or by 2 or more on the holistic score, a third grade decides. Agreement between the first two is reported, by goal, including G4, which is in Indonesian.
- **The owner's spot-check, about 10%:** 6 runs, 2 per setup, drawn by a fixed seed, exported blind for the owner to grade. Until the owner has done it, the results say plainly that the grades are a model's alone. When it's done, the owner's grade replaces the model's on those runs, and the agreement rate is published.

## What it doesn't show

- **One model, one client.** Sonnet, through Claude Code. A cheaper model could change the picture; [all Haiku](../haiku/README.md) finished nothing.
- **Six goals, three runs each.** Enough to see a big difference, and not a small one.
- **New companies,** with no memory or history, which is part of what the hierarchy is for.
- **A scripted person, not a real one.** Its fixed answers can't judge a question as a person would.
- **Unequal prompt work.** The hierarchy's prompts have been tuned over many runs. The one agent's and the swarm's were written for this benchmark, and nobody tuned them to win. They're published with the results.
- **Blinding isn't perfect.** Style can hint at the setup.
- **The builder of the hierarchy wrote these goals and rubrics.** That's why the rubrics check facts the goals state, and why a separate reviewer checked them for bias.

## Cost

On the owner's Claude plan, so it comes out of plan usage. At list price, as an estimate: about $40 to $95, for 7 company designs, the pilot's 3 runs, the 54 runs, and about 120 grades on Opus. If plan usage runs out, it pauses until it resets. It never switches to the paid API.

## What gets published

`docs/research/benchmark/`: this design; the harness; every run's work and grades, and the blind codes; and a README with the results, linked near the top of the main README. That happens whatever the results show.

## The bias review, and what was done

A separate Claude instance reviewed the goals, rubrics, and rules, given as setups A (one agent), B (flat roles taking turns, no leader, no required review), and C (a hierarchy with leaders, departments, and QA and Legal reviewers). Its notes, briefly, and what changed:

| # | Its note | Done |
|---|---|---|
| 1 | The closing section was made differently for C, from a report built for another purpose | Every setup now gets the same closing instruction, and writes the same `CLOSING.md`. C's native report is logged only |
| 2 | Only C could be stuck, retried, or cancelled into "unfinished" | C's stuck question is answered "retry" every time. Only the budget cap ends a run early, for all |
| 3 | "Finished" was undefined per setup, and work at the cap wasn't always graded | Each setup's finish is defined. Work at the cap is graded for every run, and the must-item rate over all runs is reported |
| 4 | The scripted answer coached honesty, and the setup that asked most got the coaching | That sentence is in a preamble every setup gets. The answer is now "Not available." |
| 5 | Budget answers counted toward the 5-answer cap | They don't now |
| 6 | Blinding covered only the closing section | One script strips process lines from every setup's work, and the count is reported |
| 7 | Most musts test traps, which favors review, and cost wasn't in the headline | Traps stay, because they're real. Cost per passing run and time are reported. A consistency must is added to each large goal |
| 8 | Prompt work is uneven, so A and B could be strawmen | Not fixable here. It's stated as a limit, and every prompt is published |
| 9 | "No invented facts" was a must on some goals and not others | F is now a must on every goal |
| 10 | G5's "favourite" conflicted with the honesty item | Puffery is defined, and neither credited nor penalized |
| 11 | G2's "extra service" was ambiguous | Reworded: suggesting something the customer could do is allowed |
| 12 | Word limits and the holistic score could count the closing section | They apply to the work only |
| 13 | B's answers arrived a turn late, and B could finish with a question open | The asking role goes next, and B can't finish with a question open |
| 14 | Question counts weren't comparable | Reported per run and by kind |
| Other | Too few runs for small gaps; G4 needs fluent grading; G3 had no year; log Legal's triggers | Intervals and per-goal results; grader agreement reported for G4; G3 dated Tuesday 2 March 2027; Legal's reviews reported |

Its full notes are in `bias_review.md`.
