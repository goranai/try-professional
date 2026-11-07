# Loadout accuracy

How well matching fits skills to roles. Scores are for what matching suggests, before any person decides.

| File | Contents |
|---|---|
| `real_skills.yaml` | Real public skills at pinned commits, with hand-assigned roles and categories |
| `run_real.py` | Measures them. Reads public GitHub, and needs no LLM key |
| `results_real_tune.json` | The sweep used to choose the threshold |
| `results_real_heldout.json` | A repository not used for tuning, labelled before scoring, and scored once |
| `run_rerank.py` | Measures the shortlist and the model's re-rank |
| `results.json` | The hand-written fixture in `try_professional/bench/retrieval/loadout_fixture.yaml` |
| `run_loadouts.py` | Sweeps the threshold on that fixture |

## What happened

1. The threshold was first chosen on the hand-written fixture alone: 0.68, with 26/26 on its held-out half. The same author wrote its skills and the roles' `skill_needs`, so they share vocabulary.
2. On real skills, 0.68 suggested nothing at all.
3. With wording alone, the tuning set reached about 45% precision. Code review matched Compliance and Sales.
4. Adding a person-assigned category, a penalty for one that doesn't fit, and a limit of 4 roles per skill reached 86% precision and 67% recall on the tuning set.
5. The threshold was fixed at 0.44. A separate real repository was then scored once: 10 of 11 suggestions were right, and 10 of 23 expected matches were found.

## Re-rank

Recall of 43% isn't enough for the project's goal, so embeddings now only shortlist, and the cheap model judges fit. `run_rerank.py` measures it, with categories hidden, because a fresh install won't have a person's.

Measured on 2026-11-06 on `deepseek-v4.1-flash-1017`. The target set in advance was recall of at least 60% on an unseen set. Prompt version 3 missed it, and version 4 met it.

| Set | Embeddings, with a person's categories | Re-rank, with no categories |
|---|---|---|
| **Second fresh set: 30 skills, scored once, version 4** | 21/29 precise (72%), 21/44 found (48%) | **32/50 precise (64%), 32/44 found (73%)** |
| Fresh: 30 skills, scored once, version 3 | 16/23 precise (70%), 16/39 found (41%) | 20/29 precise (69%), 20/39 found (51%) |
| Held out: 14 skills, labels seen before, version 3 | 10/11 (91%), 10/23 (43%) | 15/20 (75%), 15/23 (65%) |
| Tuning: 12 skills, prompt tuned on it, version 3 | 86%, 67% | 27/34 (79%), 27/27 (100%) |

Trust the top row. The tuning row shows how far a small set can flatter a prompt: three versions were tried on it, and 100% became 51%.

**What changed for version 4.** On the first fresh set, `engineering/backend` chose no skills at all. Rerunning that role chose 3, so identical calls could give different answers, and the first answer can't be explained because it wasn't recorded. Even then, a valid answer silently rejected most of its matches. So:

- The judge now gives a decision for every candidate, by number, instead of listing only the ones that fit. All 440 candidates got one.
- It asks for temperature 0, so answers repeat where the provider allows it.
- Every raw answer is saved beside the cache (`*.raw.jsonl`), so a surprising result can be looked at without paying for it again.
- A misshapen or empty answer is retried, instead of being read as "no".

**What to keep in mind.** Recall rose 25 points over embeddings, at a cost of 8 points of precision, and needs no person's categories. Precision is lower than version 3's, so a person's review matters more. The most common wrong suggestion was generic code review offered to every engineer, because their categories invite it. Of the 12 misses, 11 were the judge's and 1 was the shortlist's. This is one score on one set, labelled by one person, and this set may be easier: the same embeddings found 48% here against 41% on the first. One 22-role pass cost 49,710 tokens, about 2,260 per role, which is less than version 3 despite the extra decisions, because numbers replaced long skill IDs. Everything in this test cost 243,198 tokens of its 250,000 cap.

**Versions 5 and 6, measured on 2026-11-07.** Real companies were being offered Shopify skills for a physiotherapy clinic, so the judge now reads the business, and a rule rejects skills tied to a platform or industry it isn't in. These two runs reuse the second fresh set, so it's no longer unseen, and it has no business in it, so **neither run measures the new rule itself**. They only check that the change didn't hurt matching. A real rerun of 7 generations did show the rule working.

| Version | Found | Precise | Input (of which cached) | Output |
|---|---|---|---|---|
| 4 | 32/44 (73%) | 32/50 (64%) | | 49,710 total |
| 5: the rule applied even with no business | 31/44 (70%) | 31/49 (63%) | 43,235 (11,264) | 6,830 |
| 6: the rule applies only with a business | 33/44 (75%) | 33/52 (63%) | 42,861 (26,624) | 6,872 |

Version 5 lost one match, an ISO 13485 skill for the compliance reviewer. Rejections carry no reason, so the rule is the likely cause and not a proven one. Asking about "this business" with no business known was wrong in any case, so version 6 removes the rule there. That makes its prompt the same as version 4's on this set, and it still added one labelled match and one wrong one. So treat 31 to 33 of 44 as this model's run-to-run noise, even at temperature 0, and not as an improvement.

A shortlist limits recall whatever the judge does. On the two sets combined (29 skills), a size of 12 kept 38 of 50 labelled matches, and 20 kept 48, so the size is 20. That choice looked at the held-out labels, so they can't give a clean result any more. The `fresh` split comes from a third repository, was labelled before anything scored it, and is scored once.

## Limits

One person labelled every set and assigned the categories, and the real sets are small. Roles' categories were also extended while labelling the tuning set, so that set is favored. Tools were set aside to measure matching alone.

## Reproduce

```bash
python docs/research/loadouts/run_real.py tune.json tune
python docs/research/loadouts/run_real.py heldout.json heldout 0.44
python docs/research/loadouts/run_loadouts.py fixture.json
python docs/research/loadouts/run_rerank.py shortlist all       # free
TRYPRO_RERANK_APPROVED=yes python docs/research/loadouts/run_rerank.py judge tune tune.json   # paid
```
