# Skill quality: precision and coverage on a fresh labelled set

How well matching offers each role the skills it would use, measured on 2026-11-08 the way a Claude Code user's company is built: embeddings shortlist, then Claude judges each role. Precision is how many suggestions are right. Coverage is how many labelled matches are found.

## The sets

- **66 real public skills,** from three MIT-licensed repositories never used here before, at pinned commits: `addyosmani/agent-skills`, `coreyhaines31/marketingskills`, and `minhnv0807/ai-business-skills`. Earlier sets were mostly engineering skills. These are mostly marketing and business.
- **Picked by a fixed rule,** set before any skill was read: every second or fourth skill folder, alphabetically (`sample.py`). 27 make the tuning set, and 39 the held-out set.
- **Labelled by one person, the builder,** from each skill's name and description only, which is what matching reads, before anything scored them (`labels.yaml`). `labels.sha256` has the labels' fingerprint from before the first score. The company is the default one, with 22 roles.

## How it ran

- `run_quality.py` pins the skills, builds the product's shortlists of 20 per role, and has `claude -p` on `claude-sonnet-5-5` judge each role. It gets the same instructions and assignment the product hands a Claude Code session, with no tools, and in an empty folder. It ran on a Claude plan.
- Categories are hidden, and the company has no business description, as in [earlier measurements](../loadouts/README.md).
- **The tuning set** was for finding a change. **The held-out set** was scored once before the change, and once after. Its errors weren't read until both scores were in.

## What changed

The judge is told to leave out "skills that belong to a neighbouring role", but it was never shown the company's other roles, so it guessed. Copywriting went to SEO, social, and the proposal writer. **Prompt version 7 lists the company's other roles,** each with its mission and needs, one line each. That's about 1,200 more input tokens per role. The cache key includes them, so a verdict made beside one set of colleagues isn't reused beside another.

## Results

| | Suggested | Right | Precision | Coverage |
|---|---|---|---|---|
| Tuning, version 6 | 48 | 27 | 56% | 27/48 (56%) |
| Tuning, version 7 | 44 | 28 | 64% | 28/48 (58%) |
| Tuning, version 7, run again | 44 | 31 | 70% | 31/48 (65%) |
| **Held-out, version 6** | 57 | 31 | **54%** | **31/55 (56%)** |
| **Held-out, version 7** | 47 | 29 | **62%** | **29/55 (53%)** |

**What it shows.** Version 7 is more selective, and that held across runs: 44 suggestions both times on the tuning set, and 57 down to 47 on the held-out one. Precision rose about 8 points on both. **Coverage didn't change.** Two identical runs differed by 3 right matches, so the held-out set's 31 to 29 is noise.

**Where coverage is lost.**
- **The shortlist** keeps 43 of the held-out set's 55 labelled matches at 20 per role, so coverage can't pass 78% whatever the judge does. On the tuning set, 30 per role kept all 48, against 44 at 20.
- **The judge is strict with some roles.** The content writer was offered 1 of its 6 labelled skills, such as email sequences and popup copy. Its needs name blog posts, landing pages, and editing, and the judge reads them narrowly.

## What it doesn't show

- **One labeller, who also built the matcher.** Some "wrong" suggestions are arguable. The CEO was offered task breakdown, and its needs include "break a goal into a plan of work". The prompt wasn't tuned further, so that it wouldn't learn one person's judgment.
- **One model.** Version 7 is untested on the API runner's cheap models, where the judge works the same way.
- **Small sets,** and noise of about 3 matches between identical runs.

## Next

A larger shortlist, 30 per role, would raise the coverage ceiling, at about half again the judge's input. The held-out set has had its two scores, so that needs a third fresh set. The rest of these repositories' skills, which weren't sampled, would do.

## Reproduce

```bash
python docs/research/skill_quality/sample.py sampled.json
python docs/research/skill_quality/run_quality.py shortlist tune        # free
python docs/research/skill_quality/run_quality.py judge heldout <label> results_heldout.json   # Claude plan
```
